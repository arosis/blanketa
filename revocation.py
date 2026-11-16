# A whole list is downloaded rather than asking about one certificate (OCSP), so the issuer does not learn whose signature
# the user is looking at. Only certificates issued by an authority of the EU trusted lists are looked up: their list
# addresses come from that authority, not from whoever made the document, and its key authenticates the list. Lists and
# answers a document carries are only read, never fetched.
import hashlib
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509 import ocsp

import trustlist

REMEMBER_SECONDS = 600  # several signatures share an issuer; a dead server must not cost a timeout for each of them
EXPIRED_ON_CRL = x509.ObjectIdentifier("2.5.29.60")
UNREADABLE = (ValueError, TypeError, UnsupportedAlgorithm, x509.InvalidVersion)

_lock = threading.Lock()
_lists_in_memory = {}


def _unknown_status(reason):
    return {"status": "unknown", "revoked_at": None, "suspended": False, "reason": reason}


def _distribution_addresses(certificate):
    try:
        points = certificate.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
    except x509.ExtensionNotFound:
        return []
    return [name.value for point in points for name in point.full_name or [] if isinstance(name, x509.UniformResourceIdentifier)]


def _is_complete_list_of(crl, authority):
    if crl.issuer != authority.subject or not crl.is_signature_valid(authority.public_key()):
        return False
    for extension in crl.extensions:
        value = extension.value
        if isinstance(value, x509.DeltaCRLIndicator):
            return False
        if isinstance(value, x509.IssuingDistributionPoint) and (
                value.indirect_crl or value.only_some_reasons or value.only_contains_ca_certs or value.only_contains_attribute_certs):
            return False
    return True


def _is_current_complete_list(crl, authority, now):
    return (crl.next_update_utc is not None and crl.last_update_utc <= now < crl.next_update_utc
            and _is_complete_list_of(crl, authority))


def _parse_usable_list(data, authority, now):
    try:
        crl = x509.load_der_x509_crl(data)
        return crl if _is_current_complete_list(crl, authority, now) else None
    except UNREADABLE:
        return None


def _read_cached_list(path, authority, now):
    try:
        return _parse_usable_list(path.read_bytes(), authority, now)
    except OSError:
        return None


def _download_usable_list(addresses, path, authority, now):
    for address in addresses:
        try:
            data = trustlist.download_file(address)
        except Exception:  # noqa: BLE001 - any failure of this server means trying the next one
            continue
        crl = _parse_usable_list(data, authority, now)
        if crl is not None:
            try:
                trustlist.store_atomically(path, data)
            except OSError:  # a full or read-only disk only costs the next download
                pass
            return crl
    return None


def _load_usable_list(addresses, authority, folder, now):
    key = tuple(addresses)
    entry = _lists_in_memory.get(key)
    if entry and entry[0] > time.monotonic() and entry[1] is None:
        return None
    if entry and entry[1] is not None and _is_current_complete_list(entry[1], authority, now):
        return entry[1]
    path = Path(folder) / f"{hashlib.sha256(addresses[0].encode()).hexdigest()}.crl"
    crl = _read_cached_list(path, authority, now)
    if crl is None:
        crl = _download_usable_list(addresses, path, authority, now)
    _lists_in_memory[key] = (time.monotonic() + REMEMBER_SECONDS, crl)
    return crl


def _revocation_status(certificate, crl, now):
    entry = crl.get_revoked_certificate_by_serial_number(certificate.serial_number)
    reason = None
    if entry is not None:
        try:
            reason = entry.extensions.get_extension_for_class(x509.CRLReason).value.reason
        except x509.ExtensionNotFound:
            pass
    if entry is not None and reason != x509.ReasonFlags.remove_from_crl:
        return {"status": "revoked", "revoked_at": entry.revocation_date_utc.isoformat(),
                "suspended": reason == x509.ReasonFlags.certificate_hold, "reason": ""}
    # Authorities may drop expired certificates from the list, so absence only proves something while it is valid.
    if certificate.not_valid_after_utc < now and not any(extension.oid == EXPIRED_ON_CRL for extension in crl.extensions):
        return _unknown_status("Certifikát už vypršel a seznam zrušených certifikátů ho nemusí uvádět.")
    return {"status": "good", "revoked_at": None, "suspended": False, "reason": ""}


def check_certificate_revocation(der, trust_folder, folder):
    now = datetime.now(timezone.utc)
    try:
        certificate = trustlist.load_certificate_leniently(der)
        authority = trustlist.issuing_authority(der, trust_folder)
        if authority is None:
            return _unknown_status("Vydavatel certifikátu není v důvěryhodném seznamu EU, nebo se seznam nepodařilo načíst.")
        addresses = [address for address in _distribution_addresses(certificate) if address.lower().startswith(("http://", "https://"))]
        if not addresses:
            return _unknown_status("Certifikát neuvádí, kde je seznam zrušených certifikátů.")
        with _lock:
            crl = _load_usable_list(addresses, authority, folder, now)
        if crl is None:
            # An authority that has retired stops publishing, so the network need not be at fault for an old certificate.
            return _unknown_status("Certifikát už vypršel a aktuální seznam zrušených certifikátů se nepodařilo získat."
                            if certificate.not_valid_after_utc < now else
                            "Seznam zrušených certifikátů se nepodařilo načíst. Zkontrolujte připojení k internetu.")
        return _revocation_status(certificate, crl, now)
    except UNREADABLE:  # a hostile form's certificate must not break the panel
        return _unknown_status("Certifikát je poškozený, nelze ho ověřit.")


OUT_OF_FORCE_AT_THE_SIGNING = object()


def _was_out_of_force_at(moment, issued, in_force_until):
    return issued < moment and (in_force_until is None or in_force_until <= moment)


def _statement_from_file(status, kind, issued, proven_at):
    # A statement made before the signature is no proof, even while it was still in force: whoever signs with a stolen
    # key after the revocation can attach the last list that did not know of it yet. The page says it in weaker words.
    return {**status, "from_file": {"kind": kind, "issued_at": issued.isoformat(), "before_signing": issued < proven_at}}


def _what_kept_list_says(certificate, authority, data, proven_at):
    try:
        crl = x509.load_der_x509_crl(data)
        if not _is_complete_list_of(crl, authority):
            return None
        if _was_out_of_force_at(proven_at, crl.last_update_utc, crl.next_update_utc):
            return OUT_OF_FORCE_AT_THE_SIGNING
        status = _revocation_status(certificate, crl, crl.last_update_utc)
    except UNREADABLE:
        return None
    if status["status"] == "unknown":
        status = _unknown_status("Seznam zrušených certifikátů uložený v souboru vyšel až po skončení platnosti certifikátu "
                                 "a nemusí ho uvádět.")
    return _statement_from_file(status, "crl", crl.last_update_utc, proven_at)


def _key_made_signature(key, signature, signed, digest):
    try:
        if isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, signed, padding.PKCS1v15(), digest)
        elif isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, signed, ec.ECDSA(digest))
        else:
            return False
    except InvalidSignature:
        return False
    return True


def _may_answer_for(responder, authority, moment):
    try:
        responder.verify_directly_issued_by(authority)
        usages = responder.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except (*UNREADABLE, InvalidSignature, x509.ExtensionNotFound):
        return False
    return (x509.ExtendedKeyUsageOID.OCSP_SIGNING in usages
            and responder.not_valid_before_utc <= moment <= responder.not_valid_after_utc)


def _is_answer_of(response, authority):
    responders = [authority, *(named for named in response.certificates if _may_answer_for(named, authority, response.produced_at_utc))]
    return any(_key_made_signature(responder.public_key(), response.signature, response.tbs_response_bytes,
                                   response.signature_hash_algorithm) for responder in responders)


def _is_about(single, certificate, authority):
    asked = ocsp.OCSPRequestBuilder().add_certificate(certificate, authority, single.hash_algorithm).build()
    return ((single.serial_number, single.issuer_name_hash, single.issuer_key_hash)
            == (asked.serial_number, asked.issuer_name_hash, asked.issuer_key_hash))


def _what_single_answer_says(certificate, single, proven_at):
    if _was_out_of_force_at(proven_at, single.this_update_utc, single.next_update_utc):
        return OUT_OF_FORCE_AT_THE_SIGNING
    if single.certificate_status == ocsp.OCSPCertStatus.REVOKED:
        status = {"status": "revoked", "revoked_at": single.revocation_time_utc.isoformat(),
                  "suspended": single.revocation_reason == x509.ReasonFlags.certificate_hold, "reason": ""}
    elif single.certificate_status != ocsp.OCSPCertStatus.GOOD:
        return None
    elif certificate.not_valid_after_utc < single.this_update_utc:
        # Responders answer "good" about a certificate they no longer keep track of.
        status = _unknown_status("Odpověď vydavatele uložená v souboru vznikla až po skončení platnosti certifikátu.")
    else:
        status = {"status": "good", "revoked_at": None, "suspended": False, "reason": ""}
    return _statement_from_file(status, "ocsp", single.this_update_utc, proven_at)


def _what_kept_answer_says(certificate, authority, data, proven_at):
    try:
        response = ocsp.load_der_ocsp_response(data)
        if response.response_status != ocsp.OCSPResponseStatus.SUCCESSFUL or not _is_answer_of(response, authority):
            return None
        for single in response.responses:
            if _is_about(single, certificate, authority):
                return _what_single_answer_says(certificate, single, proven_at)
    except UNREADABLE:
        pass
    return None


def _strongest_statement(statements):
    said = [statement for statement in statements if isinstance(statement, dict)]
    revoked = [statement for statement in said if statement["status"] == "revoked"]
    if revoked:
        return min(revoked, key=lambda statement: statement["revoked_at"])
    good = [statement for statement in said if statement["status"] == "good"]
    if good:
        return max(good, key=lambda statement: (not statement["from_file"]["before_signing"], statement["from_file"]["issued_at"]))
    if said:
        return said[0]
    return _unknown_status("Stav certifikátu uložený v souboru v době podpisu už neplatil." if OUT_OF_FORCE_AT_THE_SIGNING in statements else
                           "Stav uložený v souboru se netýká tohoto certifikátu nebo ho nepodepsal jeho vydavatel.")


def check_revocation_kept_in_file(der, kept, proven_at, trust_folder):
    # Whoever made the file chose what it carries, so a statement counts only with its authority's signature and only
    # about a moment a time stamp proves: an old "good" proves nothing about a signature made after a revocation.
    if proven_at is None:
        return _unknown_status("Stav certifikátu uložený v souboru nejde použít, protože čas podpisu nepotvrzuje kvalifikované časové razítko.")
    try:
        certificate = trustlist.load_certificate_leniently(der)
        authority = trustlist.issuing_authority(der, trust_folder)
    except UNREADABLE:
        return _unknown_status("Certifikát je poškozený, nelze ho ověřit.")
    if authority is None:
        return _unknown_status("Stav certifikátu uložený v souboru nejde ověřit: vydavatel certifikátu není v důvěryhodném "
                               "seznamu EU, nebo se seznam nepodařilo načíst.")
    return _strongest_statement([*(_what_kept_list_says(certificate, authority, data, proven_at) for data in kept["lists"]),
                                 *(_what_kept_answer_says(certificate, authority, data, proven_at) for data in kept["answers"])])
