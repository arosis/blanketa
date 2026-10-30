"""EU trusted lists: was a certificate issued by a qualified trust service under national supervision?

System root stores do not carry the Czech qualified authorities, so a certificate the system does not trust is looked
up here. The list of trusted lists (LOTL) is authenticated by the certificates published in the Official Journal; it
names the signers of each national list, and only the list of the issuer's country is downloaded. Lists are cached in
the configuration folder, which has the same standing as the rest of the user's Blanketa data.
"""
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
EXC = "http://www.w3.org/2001/10/xml-exc-c14n#"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
DIGESTS = {"http://www.w3.org/2001/04/xmlenc#sha256": hashlib.sha256,
           "http://www.w3.org/2001/04/xmldsig-more#sha384": hashlib.sha384,
           "http://www.w3.org/2001/04/xmlenc#sha512": hashlib.sha512}
MORE = "http://www.w3.org/2001/04/xmldsig-more#"
RSA_METHODS = {MORE + "rsa-sha256": hashes.SHA256(), MORE + "rsa-sha384": hashes.SHA384(), MORE + "rsa-sha512": hashes.SHA512()}
PSS_METHODS = {f"http://www.w3.org/2007/05/xmldsig-more#{n}-rsa-MGF1": h
               for n, h in (("sha256", hashes.SHA256()), ("sha384", hashes.SHA384()), ("sha512", hashes.SHA512()))}
EC_METHODS = {MORE + "ecdsa-sha256": hashes.SHA256(), MORE + "ecdsa-sha384": hashes.SHA384(), MORE + "ecdsa-sha512": hashes.SHA512()}

CA_QC = "http://uri.etsi.org/TrstSvc/Svctype/CA/QC"
SUPERVISED = {"granted", "recognisedatnationallevel", "setbynationallaw", "accredited", "undersupervision", "supervisionincessation"}
SIGNING_USES = {"ForeSignatures", "ForeSeals"}
# Certificates carry ISO country codes, the lists name two countries differently. The UK list is closed, without updates.
TERRITORIES = {"GR": "EL", "GB": "UK"}

MAX_SIZE = 32 * 1024 * 1024
TIMEOUT = 8
REFRESH_AFTER = 24 * 3600  # lists often change long before their NextUpdate
SIGNERS_FILE = "EU-signers.txt"
REMEMBER = 600  # one form asks for each layer; a dead network must not cost a timeout every time

_lock = threading.Lock()
_memory = {}


class Unavailable(Exception):
    """No current, authentic list could be obtained."""


def _parse(data):
    root = etree.fromstring(data, etree.XMLParser(
        # A comment is outside the signature, and .text would stop at it: it could shorten what the list says.
        resolve_entities=False, no_network=True, load_dtd=False, remove_comments=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError("list with a DOCTYPE")
    return root


def _c14n(element, algorithm):
    if algorithm not in (C14N, EXC):
        raise ValueError(f"canonicalization {algorithm}")
    return etree.tostring(element, method="c14n", exclusive=algorithm == EXC, with_comments=False)


def _without_signature(root, signature):
    """The whole document as an enveloped-signature transform sees it; lxml's remove() would drop the tail too."""
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
    algorithms = [t.get("Algorithm") for t in reference.findall(f"{DS}Transforms/{DS}Transform")]
    if uri == "":
        # Everything the list says comes from this reference, so only the expected transforms are acceptable.
        if len(algorithms) != 2 or algorithms[0] != ENVELOPED:
            raise ValueError("unexpected transforms")
        target = _without_signature(root, signature)
    elif uri and uri.startswith("#") and len(algorithms) <= 1:
        found = [e for e in signature.iter() if e.get("Id") == uri[1:]]
        if len(found) != 1:
            raise ValueError(f"reference {uri}")
        target = found[0]
    else:
        raise ValueError(f"reference {uri}")
    digest = DIGESTS[reference.find(f"{DS}DigestMethod").get("Algorithm")]
    value = base64.b64decode(reference.findtext(f"{DS}DigestValue"))
    if digest(_c14n(target, algorithms[-1] if algorithms else C14N)).digest() != value:
        raise ValueError(f"digest of {uri!r} does not match")
    return uri


def verify(root, signers):
    """Raises unless the document is signed as a whole by a certificate whose SHA-256 is in signers."""
    signatures = root.findall(f"{DS}Signature")
    if len(signatures) != 1:
        raise ValueError("expected one signature")
    signature = signatures[0]
    info = signature.find(f"{DS}SignedInfo")
    uris = [_check_reference(root, signature, r) for r in info.findall(f"{DS}Reference")]
    if "" not in uris:
        raise ValueError("the signature does not cover the list")
    certificates = [base64.b64decode(e.text) for e in signature.findall(f"{DS}KeyInfo//{DS}X509Certificate")]
    der = next((c for c in certificates if hashlib.sha256(c).hexdigest() in signers), None)
    if der is None:
        raise ValueError("signed by an unknown certificate")
    key = x509.load_der_x509_certificate(der).public_key()
    method = info.find(f"{DS}SignatureMethod").get("Algorithm")
    value = base64.b64decode(signature.findtext(f"{DS}SignatureValue"))
    signed = _c14n(info, info.find(f"{DS}CanonicalizationMethod").get("Algorithm"))
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


def _scheme(root):
    return root.find(f"{TSL}SchemeInformation")


def _when(text):
    return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))


def _check_current(root, territory, now=None):
    info = _scheme(root)
    if info.findtext(f"{TSL}SchemeTerritory") != territory:
        raise ValueError("list of another country")
    if _when(info.findtext(f"{TSL}NextUpdate/{TSL}dateTime")) < (now or datetime.now(timezone.utc)):
        raise ValueError("list past its next update")


def _sequence(root):
    return int(_scheme(root).findtext(f"{TSL}TSLSequenceNumber"))


def pointers(lotl):
    """{territory: (XML list location, SHA-256 of the certificates allowed to sign it)} from an authentic LOTL."""
    result = {}
    for pointer in _scheme(lotl).findall(f"{TSL}PointersToOtherTSL/{TSL}OtherTSLPointer"):
        details = {e.tag.rsplit("}")[-1]: e.text for e in pointer.iter() if isinstance(e.tag, str)}
        if "xml" not in (details.get("MimeType") or ""):
            continue
        signers = {hashlib.sha256(base64.b64decode(e.text)).hexdigest() for e in pointer.iter(f"{TSL}X509Certificate")}
        result[details["SchemeTerritory"]] = (pointer.findtext(f"{TSL}TSLLocation"), signers)
    return result


def download(url):
    if os.environ.get("ZFO_OFFLINE"):
        raise OSError("offline")
    if urlparse(url).scheme not in ("http", "https"):  # some lists are on plain HTTP; their signature is what counts
        raise OSError(f"list location {url}")
    # The system's certificates too: offices with a TLS-inspecting proxy have its authority only there.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    request = urllib.request.Request(url, headers={"User-Agent": "Blanketa"})
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:  # noqa: S310 - scheme checked above
        data = response.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise OSError("list too large")
    return data


def store(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(handle, "wb") as out:
        out.write(data)
    os.replace(temporary, path)


def _read_cached(path, territory, signers):
    try:
        root = _parse(path.read_bytes())
        verify(root, signers)
        _check_current(root, territory)
        return root
    except (OSError, ValueError, KeyError, TypeError, AttributeError, InvalidSignature, UnsupportedAlgorithm, x509.InvalidVersion, etree.LxmlError):
        return None


def _refresh(path, territory, url, signers, cached):
    """Downloads, authenticates and stores a list; never replaces a cached list with an older one."""
    data = download(url)
    root = _parse(data)
    verify(root, signers)
    _check_current(root, territory)
    if cached is not None and _sequence(root) < _sequence(cached):
        raise ValueError("list older than the cached one")
    store(path, data)
    if territory == "EU":
        store(path.with_name(SIGNERS_FILE), "\n".join(sorted(pointers(root).get("EU", ("", set()))[1])).encode())
    return root


def _refresh_quietly(path, territory, url, signers, cached):
    try:
        _refresh(path, territory, url, signers, cached)
    except Exception:  # noqa: BLE001 - the cached list stays in use, the next window tries again
        pass


def _load(folder, territory, url, signers):
    """An authentic current list: the cached one, refreshed behind it when old, otherwise a download."""
    path = Path(folder) / f"{territory}.xml"
    cached = _read_cached(path, territory, signers)
    if cached is not None:
        if time.time() - path.stat().st_mtime > REFRESH_AFTER:
            # Opening a form must not wait for this. A failed try also waits a day, or every form would retry it.
            os.utime(path)
            threading.Thread(target=_refresh_quietly, args=(path, territory, url, signers, cached), daemon=True).start()
        return cached
    return _refresh(path, territory, url, signers, None)


def _remembered(folder, territory, load):
    entry = _memory.get((str(folder), territory))
    if entry and entry[0] > time.monotonic():
        if entry[1] is None:
            raise Unavailable(territory)
        return entry[1]
    try:
        root = load()
    except Exception as e:  # noqa: BLE001 - network, XML and signature failures all mean the same to the reader
        _memory[(str(folder), territory)] = (time.monotonic() + REMEMBER, None)
        raise Unavailable(territory) from e
    _memory[(str(folder), territory)] = (time.monotonic() + REMEMBER, root)
    return root


def _lotl(folder):
    return _remembered(folder, "EU", lambda: _load(folder, "EU", LOTL_URL, _lotl_signers(folder)))


def _lotl_signers(folder):
    """The pinned signers and those a verified LOTL announced, which is how they change without a new release."""
    try:
        announced = (Path(folder) / SIGNERS_FILE).read_text(encoding="ascii").split()
    except (OSError, ValueError):
        announced = []
    return set(PINNED_SIGNERS) | {a for a in announced if re.fullmatch(r"[0-9a-f]{64}", a)}


def _national(folder, territory):
    def load():
        found = pointers(_lotl(folder)).get(territory)
        if found is None:
            raise ValueError("no list for this country")
        return _load(folder, territory, found[0], found[1])
    return _remembered(folder, territory, load)


def _status_at(service, moment):
    """The service's status at a moment, from the current entry and its history."""
    information = service.find(f"{TSL}ServiceInformation")
    timeline = [information, *service.findall(f"{TSL}ServiceHistory/{TSL}ServiceHistoryInstance")]
    entries = sorted(((_when(e.findtext(f"{TSL}StatusStartingTime")), e.findtext(f"{TSL}ServiceStatus")) for e in timeline),
                     key=lambda entry: entry[0])
    status = None
    for start, value in entries:
        if start <= moment:
            status = value
    return status.rsplit("/", 1)[-1] if status else None


def _for_signing(information):
    uses = {u.text.rsplit("/", 1)[-1] for u in information.findall(
        f"{TSL}ServiceInformationExtensions/{TSL}Extension/{TSL}AdditionalServiceInformation/{TSL}URI") if "/SvcInfoExt/For" in (u.text or "")}
    return not uses or bool(uses & SIGNING_USES)


def _usable_for_signing(certificate):
    try:
        usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return True
    return usage.content_commitment or usage.digital_signature


def _standing(root, certificate):
    """(standing, authority) of the certificate's issuer: "listed", "withdrawn" (supervised at issue, no longer) or None."""
    moment = certificate.not_valid_before_utc
    now = datetime.now(timezone.utc)
    standing, issuer_found = None, None
    providers = root.findall(f"{TSL}TrustServiceProviderList/{TSL}TrustServiceProvider")  # never .iter(): the signature's objects are not covered
    for service in (s for p in providers for s in p.findall(f"{TSL}TSPServices/{TSL}TSPService")):
        information = service.find(f"{TSL}ServiceInformation")
        if information.findtext(f"{TSL}ServiceTypeIdentifier") != CA_QC or not _for_signing(information):
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


def issuer(der, folder):
    """The authority of the trusted lists that issued a DER certificate; None when none did or the lists are unavailable."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            certificate = x509.load_der_x509_certificate(der)
        country = certificate.issuer.get_attributes_for_oid(x509.NameOID.COUNTRY_NAME)[0].value.upper()
        territory = TERRITORIES.get(country, country)
        if not re.fullmatch(r"[A-Z]{2}", territory) or territory in ("EU", "UK"):
            return None
        with _lock:
            if territory not in pointers(_lotl(folder)):
                return None
            root = _national(folder, territory)
        return _standing(root, certificate)[1]
    except (Unavailable, IndexError, ValueError, TypeError, AttributeError, UnsupportedAlgorithm, x509.InvalidVersion):
        return None


def check(der, folder):
    """Verdict for one DER certificate: {"trusted": bool, "unverifiable": bool, "reason": str}."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # old certificates in forms break minor encoding rules
            certificate = x509.load_der_x509_certificate(der)
        return _judge(certificate, folder)
    except (ValueError, TypeError, UnsupportedAlgorithm, x509.InvalidVersion):  # a hostile form's certificate must not stop the form opening
        return {"trusted": False, "unverifiable": False, "reason": "Certifikát je poškozený, nelze ho ověřit."}


def _judge(certificate, folder):
    countries = certificate.issuer.get_attributes_for_oid(x509.NameOID.COUNTRY_NAME)
    country = countries[0].value.upper() if countries else ""
    if not re.fullmatch(r"[A-Z]{2}", country):  # it also names a file, and it comes from the signed document
        return {"trusted": False, "unverifiable": False, "reason": "Vydavatel certifikátu neuvádí zemi z EU."}
    territory = TERRITORIES.get(country, country)
    try:
        with _lock:
            if territory in ("EU", "UK") or territory not in pointers(_lotl(folder)):
                return {"trusted": False, "unverifiable": False, "reason": "Země vydavatele nemá důvěryhodný seznam EU."}
            root = _national(folder, territory)
    except Unavailable:
        return {"trusted": False, "unverifiable": True,
                "reason": "Důvěryhodný seznam EU se nepodařilo načíst, certifikát proto nelze ověřit. Zkontrolujte připojení k internetu."}
    usable = _usable_for_signing(certificate)
    try:
        standing = _standing(root, certificate)[0] if usable else None
    except (AttributeError, ValueError, TypeError):  # the list is authentic but not as expected; not the certificate's fault
        return {"trusted": False, "unverifiable": True, "reason": "Důvěryhodný seznam EU má neočekávaný obsah, certifikát proto nelze ověřit."}
    if standing == "listed":
        return {"trusted": True, "unverifiable": False, "reason": ""}
    if standing == "withdrawn":
        return {"trusted": False, "unverifiable": True,
                "reason": "Vydavatel certifikátu už není pod dohledem, důvěryhodnost proto nelze potvrdit."}
    return {"trusted": False, "unverifiable": False, "reason": "Vydavatel certifikátu není v důvěryhodném seznamu EU."}
