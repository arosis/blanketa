"""Has a certificate's issuer revoked it? Answered from the issuer's certificate revocation list (CRL).

A whole list is downloaded rather than asking about one certificate (OCSP), so the issuer does not learn whose signature
the user is looking at. Only certificates issued by an authority of the EU trusted lists are looked up: their list
addresses come from that authority, not from whoever made the document, and its key authenticates the list.
"""
import hashlib
import threading
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm

import trustlist

REMEMBER = 600  # several signatures share an issuer; a dead server must not cost a timeout for each of them
EXPIRED_ON_CRL = x509.ObjectIdentifier("2.5.29.60")

_lock = threading.Lock()
_memory = {}


def _unknown(reason):
    return {"status": "unknown", "revoked_at": None, "suspended": False, "reason": reason}


def _addresses(certificate):
    try:
        points = certificate.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
    except x509.ExtensionNotFound:
        return []
    return [n.value for p in points for n in p.full_name or [] if isinstance(n, x509.UniformResourceIdentifier)]


def _usable(crl, authority, now):
    """Is this the authority's current, complete list?"""
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


def _read(data, authority, now):
    """The parsed list if it is usable, otherwise None."""
    try:
        crl = x509.load_der_x509_crl(data)
        return crl if _usable(crl, authority, now) else None
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return None


def _load(addresses, authority, folder, now):
    """A usable list: from memory, the cache or the first address that answers. None when there is none."""
    key = tuple(addresses)
    entry = _memory.get(key)
    if entry and entry[0] > time.monotonic() and entry[1] is None:
        return None
    if entry and entry[1] is not None and _usable(entry[1], authority, now):
        return entry[1]
    path = Path(folder) / f"{hashlib.sha256(addresses[0].encode()).hexdigest()}.crl"
    crl = None
    try:
        crl = _read(path.read_bytes(), authority, now)
    except OSError:
        pass
    for address in addresses if crl is None else ():
        try:
            data = trustlist.download(address)
        except Exception:  # noqa: BLE001 - any failure of this server means trying the next one
            continue
        crl = _read(data, authority, now)
        if crl is not None:
            try:
                trustlist.store(path, data)
            except OSError:  # a full or read-only disk only costs the next download
                pass
            break
    _memory[key] = (time.monotonic() + REMEMBER, crl)
    return crl


def _status(certificate, crl, now):
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
    if certificate.not_valid_after_utc < now and not any(e.oid == EXPIRED_ON_CRL for e in crl.extensions):
        return _unknown("Certifikát už vypršel a seznam zrušených certifikátů ho nemusí uvádět.")
    return {"status": "good", "revoked_at": None, "suspended": False, "reason": ""}


def check(der, trust_folder, folder):
    """{"status": "good" | "revoked" | "unknown", "revoked_at": ISO time or None, "suspended": bool, "reason": str}."""
    now = datetime.now(timezone.utc)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # old certificates in forms break minor encoding rules
            certificate = x509.load_der_x509_certificate(der)
        authority = trustlist.issuer(der, trust_folder)
        if authority is None:
            return _unknown("Vydavatel certifikátu není v důvěryhodném seznamu EU, nebo se seznam nepodařilo načíst.")
        addresses = [a for a in _addresses(certificate) if a.lower().startswith(("http://", "https://"))]
        if not addresses:
            return _unknown("Certifikát neuvádí, kde je seznam zrušených certifikátů.")
        with _lock:
            crl = _load(addresses, authority, folder, now)
        if crl is None:
            # An authority that has retired stops publishing, so the network need not be at fault for an old certificate.
            return _unknown("Certifikát už vypršel a aktuální seznam zrušených certifikátů se nepodařilo získat."
                            if certificate.not_valid_after_utc < now else
                            "Seznam zrušených certifikátů se nepodařilo načíst. Zkontrolujte připojení k internetu.")
        return _status(certificate, crl, now)
    except (ValueError, TypeError, UnsupportedAlgorithm):  # a hostile form's certificate must not break the panel
        return _unknown("Certifikát je poškozený, nelze ho ověřit.")
