# A whole list is downloaded rather than asking about one certificate (OCSP), so the issuer does not learn whose signature
# the user is looking at. Only certificates issued by an authority of the EU trusted lists are looked up: their list
# addresses come from that authority, not from whoever made the document, and its key authenticates the list.
import hashlib
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm

import trustlist

REMEMBER_SECONDS = 600  # several signatures share an issuer; a dead server must not cost a timeout for each of them
EXPIRED_ON_CRL = x509.ObjectIdentifier("2.5.29.60")

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


def _is_current_complete_list(crl, authority, now):
    if crl.issuer != authority.subject or not crl.is_signature_valid(authority.public_key()):
        return False
    if crl.next_update_utc is None or not crl.last_update_utc <= now < crl.next_update_utc:
        return False
    for extension in crl.extensions:
        value = extension.value
        if isinstance(value, x509.DeltaCRLIndicator):
            return False
        if isinstance(value, x509.IssuingDistributionPoint) and (
                value.indirect_crl or value.only_some_reasons or value.only_contains_ca_certs or value.only_contains_attribute_certs):
            return False
    return True


def _parse_usable_list(data, authority, now):
    try:
        crl = x509.load_der_x509_crl(data)
        return crl if _is_current_complete_list(crl, authority, now) else None
    except (ValueError, TypeError, UnsupportedAlgorithm, x509.InvalidVersion):
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
    except (ValueError, TypeError, UnsupportedAlgorithm, x509.InvalidVersion):  # a hostile form's certificate must not break the panel
        return _unknown_status("Certifikát je poškozený, nelze ho ověřit.")
