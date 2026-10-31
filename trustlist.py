# System root stores do not carry the Czech qualified authorities, so a certificate the system does not trust is looked
# up here. The LOTL is authenticated by certificates published in the Official Journal; it names the signers of each
# national list, and only the list of the issuer's country is downloaded.
import base64
import copy
import hashlib
import os
import re
import ssl
import tempfile
import threading
import time
import urllib.request
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import certifi
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from lxml import etree

LOTL_URL = "https://ec.europa.eu/tools/lotl/eu-lotl.xml"
# LOTL-signing certificates from the Official Journal, C/2026/1944.
PINNED_SIGNERS = frozenset({
    "c0641c4f7d56c431b1c924742db7fce9c1eef7d7fd212113a2768486b3abcdc5",
    "e0a620fbb6747362bb933ac44169d676a553444716cf5f31605f12a22b8396b1",
    "df7e29360c34b2b8d6d5f40325c1d4d12c9922cecd33b7407674a74b2b3ca1e5",
    "b63d416744e7098bf9ec2caa596a93bc2468e37f8284ba65ecc061711bcbaa18",
    "236103f03a8031ae8f47f9059bf8de38564cdbfebedde4a597d50f8980aa653b",
    "d2064fdd70f6982dcc516b86d9d5c56aea939417c624b2e478c0b29de54f8474",
})

TSL = "{http://uri.etsi.org/02231/v2#}"
DS = "{http://www.w3.org/2000/09/xmldsig#}"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
EXCLUSIVE_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
DIGESTS = {"http://www.w3.org/2001/04/xmlenc#sha256": hashlib.sha256,
           "http://www.w3.org/2001/04/xmldsig-more#sha384": hashlib.sha384,
           "http://www.w3.org/2001/04/xmlenc#sha512": hashlib.sha512}
XMLDSIG_MORE = "http://www.w3.org/2001/04/xmldsig-more#"
RSA_METHODS = {XMLDSIG_MORE + "rsa-sha256": hashes.SHA256(), XMLDSIG_MORE + "rsa-sha384": hashes.SHA384(), XMLDSIG_MORE + "rsa-sha512": hashes.SHA512()}
PSS_METHODS = {f"http://www.w3.org/2007/05/xmldsig-more#{n}-rsa-MGF1": h
               for n, h in (("sha256", hashes.SHA256()), ("sha384", hashes.SHA384()), ("sha512", hashes.SHA512()))}
EC_METHODS = {XMLDSIG_MORE + "ecdsa-sha256": hashes.SHA256(), XMLDSIG_MORE + "ecdsa-sha384": hashes.SHA384(), XMLDSIG_MORE + "ecdsa-sha512": hashes.SHA512()}

CA_QC = "http://uri.etsi.org/TrstSvc/Svctype/CA/QC"
TSA_QTST = "http://uri.etsi.org/TrstSvc/Svctype/TSA/QTST"
SUPERVISED = {"granted", "recognisedatnationallevel", "setbynationallaw", "accredited", "undersupervision", "supervisionincessation"}
SIGNING_USES = {"ForeSignatures", "ForeSeals"}
LIST_TERRITORY_BY_ISO_COUNTRY = {"GR": "EL", "GB": "UK"}
# EU is the list of lists itself; the UK list is closed, without updates.
TERRITORIES_WITHOUT_LIST = ("EU", "UK")

MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 8
REFRESH_AFTER_SECONDS = 24 * 3600  # lists often change long before their NextUpdate
SIGNERS_FILE = "EU-signers.txt"
REMEMBER_SECONDS = 600  # one form asks for each layer; a dead network must not cost a timeout every time

_lock = threading.Lock()
_lists_in_memory = {}


class Unavailable(Exception):
    pass


def _parse_list_xml(data):
    root = etree.fromstring(data, etree.XMLParser(
        # A comment is outside the signature, and .text would stop at it: it could shorten what the list says.
        resolve_entities=False, no_network=True, load_dtd=False, remove_comments=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError("list with a DOCTYPE")
    return root


def _canonicalize(element, algorithm):
    if algorithm not in (C14N, EXCLUSIVE_C14N):
        raise ValueError(f"canonicalization {algorithm}")
    return etree.tostring(element, method="c14n", exclusive=algorithm == EXCLUSIVE_C14N, with_comments=False)


def _without_signature(root, signature):
    # As an enveloped-signature transform sees the document; lxml's remove() would drop the tail too.
    clone = copy.deepcopy(root)
    node = clone[root.index(signature)]
    if node.tail:
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + node.tail
        else:
            clone.text = (clone.text or "") + node.tail
    clone.remove(node)
    return clone


def _check_reference(root, signature, reference):
    uri = reference.get("URI")
    algorithms = [transform.get("Algorithm") for transform in reference.findall(f"{DS}Transforms/{DS}Transform")]
    if uri == "":
        # Everything the list says comes from this reference, so only the expected transforms are acceptable.
        if len(algorithms) != 2 or algorithms[0] != ENVELOPED:
            raise ValueError("unexpected transforms")
        target = _without_signature(root, signature)
    elif uri and uri.startswith("#") and len(algorithms) <= 1:
        found = [element for element in signature.iter() if element.get("Id") == uri[1:]]
        if len(found) != 1:
            raise ValueError(f"reference {uri}")
        target = found[0]
    else:
        raise ValueError(f"reference {uri}")
    digest = DIGESTS[reference.find(f"{DS}DigestMethod").get("Algorithm")]
    value = base64.b64decode(reference.findtext(f"{DS}DigestValue"))
    if digest(_canonicalize(target, algorithms[-1] if algorithms else C14N)).digest() != value:
        raise ValueError(f"digest of {uri!r} does not match")
    return uri


def _verify_signature_value(key, method, value, signed):
    if method in RSA_METHODS and isinstance(key, rsa.RSAPublicKey):
        key.verify(value, signed, padding.PKCS1v15(), RSA_METHODS[method])
    elif method in PSS_METHODS and isinstance(key, rsa.RSAPublicKey):
        algorithm = PSS_METHODS[method]
        key.verify(value, signed, padding.PSS(padding.MGF1(algorithm), algorithm.digest_size), algorithm)
    elif method in EC_METHODS and isinstance(key, ec.EllipticCurvePublicKey):
        half = len(value) // 2
        der_value = encode_dss_signature(int.from_bytes(value[:half], "big"), int.from_bytes(value[half:], "big"))
        key.verify(der_value, signed, ec.ECDSA(EC_METHODS[method]))
    else:
        raise ValueError(f"signature method {method}")


def verify_list_signature(root, signers):
    signatures = root.findall(f"{DS}Signature")
    if len(signatures) != 1:
        raise ValueError("expected one signature")
    signature = signatures[0]
    info = signature.find(f"{DS}SignedInfo")
    uris = [_check_reference(root, signature, reference) for reference in info.findall(f"{DS}Reference")]
    if "" not in uris:
        raise ValueError("the signature does not cover the list")
    certificates = [base64.b64decode(element.text) for element in signature.findall(f"{DS}KeyInfo//{DS}X509Certificate")]
    der = next((candidate for candidate in certificates if hashlib.sha256(candidate).hexdigest() in signers), None)
    if der is None:
        raise ValueError("signed by an unknown certificate")
    key = x509.load_der_x509_certificate(der).public_key()
    method = info.find(f"{DS}SignatureMethod").get("Algorithm")
    value = base64.b64decode(signature.findtext(f"{DS}SignatureValue"))
    signed = _canonicalize(info, info.find(f"{DS}CanonicalizationMethod").get("Algorithm"))
    _verify_signature_value(key, method, value, signed)


def _scheme_information(root):
    return root.find(f"{TSL}SchemeInformation")


def _parse_time(text):
    return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))


def _check_list_is_current(root, territory, now=None):
    info = _scheme_information(root)
    if info.findtext(f"{TSL}SchemeTerritory") != territory:
        raise ValueError("list of another country")
    if _parse_time(info.findtext(f"{TSL}NextUpdate/{TSL}dateTime")) < (now or datetime.now(timezone.utc)):
        raise ValueError("list past its next update")


def _sequence_number(root):
    return int(_scheme_information(root).findtext(f"{TSL}TSLSequenceNumber"))


def national_list_pointers(lotl):
    result = {}
    for pointer in _scheme_information(lotl).findall(f"{TSL}PointersToOtherTSL/{TSL}OtherTSLPointer"):
        details = {element.tag.rsplit("}")[-1]: element.text for element in pointer.iter() if isinstance(element.tag, str)}
        if "xml" not in (details.get("MimeType") or ""):
            continue
        signers = {hashlib.sha256(base64.b64decode(element.text)).hexdigest() for element in pointer.iter(f"{TSL}X509Certificate")}
        result[details["SchemeTerritory"]] = (pointer.findtext(f"{TSL}TSLLocation"), signers)
    return result


def download_file(url):
    if os.environ.get("ZFO_OFFLINE"):
        raise OSError("offline")
    if urlparse(url).scheme not in ("http", "https"):  # some lists are on plain HTTP; their signature is what counts
        raise OSError(f"list location {url}")
    # The system's certificates too: offices with a TLS-inspecting proxy have its authority only there.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    request = urllib.request.Request(url, headers={"User-Agent": "Blanketa"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS, context=context) as response:  # noqa: S310 - scheme checked above
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise OSError("list too large")
    return data


def store_atomically(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(handle, "wb") as out:
        out.write(data)
    os.replace(temporary, path)


def _read_cached(path, territory, signers):
    try:
        root = _parse_list_xml(path.read_bytes())
        verify_list_signature(root, signers)
        _check_list_is_current(root, territory)
        return root
    except (OSError, ValueError, KeyError, TypeError, AttributeError, InvalidSignature, UnsupportedAlgorithm, x509.InvalidVersion, etree.LxmlError):
        return None


def _download_and_store_list(path, territory, url, signers, cached):
    data = download_file(url)
    root = _parse_list_xml(data)
    verify_list_signature(root, signers)
    _check_list_is_current(root, territory)
    if cached is not None and _sequence_number(root) < _sequence_number(cached):
        raise ValueError("list older than the cached one")
    store_atomically(path, data)
    if territory == "EU":
        store_atomically(path.with_name(SIGNERS_FILE), "\n".join(sorted(national_list_pointers(root).get("EU", ("", set()))[1])).encode())
    return root


def _refresh_list_quietly(path, territory, url, signers, cached):
    try:
        _download_and_store_list(path, territory, url, signers, cached)
    except Exception:  # noqa: BLE001 - the cached list stays in use, the next window tries again
        pass


def _load_list(folder, territory, url, signers):
    path = Path(folder) / f"{territory}.xml"
    cached = _read_cached(path, territory, signers)
    if cached is not None:
        if time.time() - path.stat().st_mtime > REFRESH_AFTER_SECONDS:
            # Opening a form must not wait for this. A failed try also waits a day, or every form would retry it.
            os.utime(path)
            threading.Thread(target=_refresh_list_quietly, args=(path, territory, url, signers, cached), daemon=True).start()
        return cached
    return _download_and_store_list(path, territory, url, signers, None)


def _remembered_list(folder, territory, load):
    entry = _lists_in_memory.get((str(folder), territory))
    if entry and entry[0] > time.monotonic():
        if entry[1] is None:
            raise Unavailable(territory)
        return entry[1]
    try:
        root = load()
    except Exception as error:  # noqa: BLE001 - network, XML and signature failures all mean the same to the reader
        _lists_in_memory[(str(folder), territory)] = (time.monotonic() + REMEMBER_SECONDS, None)
        raise Unavailable(territory) from error
    _lists_in_memory[(str(folder), territory)] = (time.monotonic() + REMEMBER_SECONDS, root)
    return root


def _load_lotl(folder):
    return _remembered_list(folder, "EU", lambda: _load_list(folder, "EU", LOTL_URL, _lotl_signers(folder)))


def _lotl_signers(folder):
    # A verified LOTL announces its next signers, which is how they change without a new release.
    try:
        announced = (Path(folder) / SIGNERS_FILE).read_text(encoding="ascii").split()
    except (OSError, ValueError):
        announced = []
    return set(PINNED_SIGNERS) | {signer for signer in announced if re.fullmatch(r"[0-9a-f]{64}", signer)}


def _load_national_list(folder, territory):
    def load():
        found = national_list_pointers(_load_lotl(folder)).get(territory)
        if found is None:
            raise ValueError("no list for this country")
        return _load_list(folder, territory, found[0], found[1])
    return _remembered_list(folder, territory, load)


def _status_at(service, moment):
    information = service.find(f"{TSL}ServiceInformation")
    timeline = [information, *service.findall(f"{TSL}ServiceHistory/{TSL}ServiceHistoryInstance")]
    entries = sorted(((_parse_time(entry.findtext(f"{TSL}StatusStartingTime")), entry.findtext(f"{TSL}ServiceStatus")) for entry in timeline),
                     key=lambda entry: entry[0])
    status = None
    for start, value in entries:
        if start <= moment:
            status = value
    return status.rsplit("/", 1)[-1] if status else None


def _is_service_for_signing(information):
    uses = {uri.text.rsplit("/", 1)[-1] for uri in information.findall(
        f"{TSL}ServiceInformationExtensions/{TSL}Extension/{TSL}AdditionalServiceInformation/{TSL}URI") if "/SvcInfoExt/For" in (uri.text or "")}
    return not uses or bool(uses & SIGNING_USES)


def _can_certificate_sign(certificate):
    try:
        usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return True
    return usage.content_commitment or usage.digital_signature


def _trust_services(root):
    # Never .iter(): the signature's objects are not covered.
    providers = root.findall(f"{TSL}TrustServiceProviderList/{TSL}TrustServiceProvider")
    return (service for provider in providers for service in provider.findall(f"{TSL}TSPServices/{TSL}TSPService"))


def _issuer_standing(root, certificate):
    moment = certificate.not_valid_before_utc
    now = datetime.now(timezone.utc)
    standing, issuer_found = None, None
    for service in _trust_services(root):
        information = service.find(f"{TSL}ServiceInformation")
        if information.findtext(f"{TSL}ServiceTypeIdentifier") != CA_QC or not _is_service_for_signing(information):
            continue
        for element in information.findall(f"{TSL}ServiceDigitalIdentity/{TSL}DigitalId/{TSL}X509Certificate"):
            try:
                authority = x509.load_der_x509_certificate(base64.b64decode(element.text))
            except ValueError:  # one odd entry must not cost the other services of the list
                continue
            if authority.subject != certificate.issuer or not authority.not_valid_before_utc <= moment <= authority.not_valid_after_utc:
                continue
            try:
                certificate.verify_directly_issued_by(authority)
            except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
                continue
            if _status_at(service, moment) in SUPERVISED:
                # The date of issue comes from the certificate. A withdrawn authority's key may be out of anyone's
                # control, and with revocation unchecked (#9) a backdated certificate would pass, so it only warns.
                if _status_at(service, now) in SUPERVISED:
                    return "listed", authority
                standing, issuer_found = "withdrawn", authority
    return standing, issuer_found


def load_certificate_leniently(der):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # old certificates in forms break minor encoding rules
        return x509.load_der_x509_certificate(der)


def _national_list_if_any(folder, territory):
    with _lock:
        if territory in TERRITORIES_WITHOUT_LIST or territory not in national_list_pointers(_load_lotl(folder)):
            return None
        return _load_national_list(folder, territory)


def issuing_authority(der, folder):
    try:
        certificate = load_certificate_leniently(der)
        country = certificate.issuer.get_attributes_for_oid(x509.NameOID.COUNTRY_NAME)[0].value.upper()
        territory = LIST_TERRITORY_BY_ISO_COUNTRY.get(country, country)
        if not re.fullmatch(r"[A-Z]{2}", territory):
            return None
        root = _national_list_if_any(folder, territory)
        return None if root is None else _issuer_standing(root, certificate)[1]
    except (Unavailable, IndexError, ValueError, TypeError, AttributeError, UnsupportedAlgorithm, x509.InvalidVersion):
        return None


def check_certificate(der, folder):
    try:
        return _judge_certificate(load_certificate_leniently(der), folder)
    except (ValueError, TypeError, UnsupportedAlgorithm, x509.InvalidVersion):  # a hostile form's certificate must not stop the form opening
        return {"trusted": False, "unverifiable": False, "reason": "Certifikát je poškozený, nelze ho ověřit."}


def _only_for_stamping(certificate):
    # RFC 3161 demands this of a stamping unit. Authorities listed for stamps can issue people's certificates too, and
    # their holders must not be able to stamp any time they like.
    try:
        usage = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound:
        return False
    return usage.critical and list(usage.value) == [x509.ExtendedKeyUsageOID.TIME_STAMPING]


def _is_stamp_certificate_qualified(root, certificate, moment):
    # Units are retired routinely and their old stamps stay good, so only the status at the stamp's time counts.
    if not certificate.not_valid_before_utc <= moment <= certificate.not_valid_after_utc:
        return False
    for service in _trust_services(root):
        information = service.find(f"{TSL}ServiceInformation")
        if information.findtext(f"{TSL}ServiceTypeIdentifier") != TSA_QTST or _status_at(service, moment) not in SUPERVISED:
            continue
        for element in information.findall(f"{TSL}ServiceDigitalIdentity/{TSL}DigitalId/{TSL}X509Certificate"):
            try:
                listed = x509.load_der_x509_certificate(base64.b64decode(element.text))
                if listed == certificate:
                    return True
                if listed.subject == certificate.issuer and _only_for_stamping(certificate):
                    certificate.verify_directly_issued_by(listed)
                    return True
            except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
                continue
    return False


def check_stamp_certificate(der, moment, folder):
    refused = {"qualified": False, "unverifiable": False,
               "reason": "Vydavatel časového razítka není kvalifikovaný podle důvěryhodného seznamu EU."}
    try:
        certificate = load_certificate_leniently(der)
        countries = certificate.issuer.get_attributes_for_oid(x509.NameOID.COUNTRY_NAME)
        country = countries[0].value.upper() if countries else ""
        territory = LIST_TERRITORY_BY_ISO_COUNTRY.get(country, country)
        if not re.fullmatch(r"[A-Z]{2}", territory):
            return refused
        root = _national_list_if_any(folder, territory)
        if root is None:
            return refused
        if _is_stamp_certificate_qualified(root, certificate, moment):
            return {"qualified": True, "unverifiable": False, "reason": ""}
        return refused
    except Unavailable:
        return {"qualified": False, "unverifiable": True,
                "reason": "Důvěryhodný seznam EU se nepodařilo načíst, časové razítko proto nelze ověřit. Zkontrolujte připojení k internetu."}
    except (AttributeError, IndexError, ValueError, TypeError, UnsupportedAlgorithm, x509.InvalidVersion):
        return {**refused, "reason": "Certifikát časového razítka se nepodařilo ověřit."}


def _judge_certificate(certificate, folder):
    countries = certificate.issuer.get_attributes_for_oid(x509.NameOID.COUNTRY_NAME)
    country = countries[0].value.upper() if countries else ""
    if not re.fullmatch(r"[A-Z]{2}", country):  # it also names a file, and it comes from the signed document
        return {"trusted": False, "unverifiable": False, "reason": "Vydavatel certifikátu neuvádí zemi z EU."}
    territory = LIST_TERRITORY_BY_ISO_COUNTRY.get(country, country)
    try:
        root = _national_list_if_any(folder, territory)
        if root is None:
            return {"trusted": False, "unverifiable": False, "reason": "Země vydavatele nemá důvěryhodný seznam EU."}
    except Unavailable:
        return {"trusted": False, "unverifiable": True,
                "reason": "Důvěryhodný seznam EU se nepodařilo načíst, certifikát proto nelze ověřit. Zkontrolujte připojení k internetu."}
    usable = _can_certificate_sign(certificate)
    try:
        standing = _issuer_standing(root, certificate)[0] if usable else None
    except (AttributeError, ValueError, TypeError):  # the list is authentic but not as expected; not the certificate's fault
        return {"trusted": False, "unverifiable": True, "reason": "Důvěryhodný seznam EU má neočekávaný obsah, certifikát proto nelze ověřit."}
    if standing == "listed":
        return {"trusted": True, "unverifiable": False, "reason": ""}
    if standing == "withdrawn":
        return {"trusted": False, "unverifiable": True,
                "reason": "Vydavatel certifikátu už není pod dohledem, důvěryhodnost proto nelze potvrdit."}
    return {"trusted": False, "unverifiable": False, "reason": "Vydavatel certifikátu není v důvěryhodném seznamu EU."}
