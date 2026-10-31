import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

import keychain
import revocation
import trustlist

ROOT = Path(__file__).resolve().parent
RESOURCES = Path(getattr(sys, "_MEIPASS", ROOT))
IS_MAC, IS_WINDOWS = sys.platform == "darwin", sys.platform == "win32"


def config_dir():
    if IS_MAC:
        base = Path.home() / "Library/Application Support/Blanketa"
    elif IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home())) / "Blanketa"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "blanketa"
    base.mkdir(parents=True, exist_ok=True)
    return base


FROZEN = hasattr(sys, "_MEIPASS")
TEST_PASSWORD = os.environ.get("ZFO_TEST_KEYCHAIN_PASSWORD", "test")
TEST_KEYCHAIN = Path(os.environ.get("ZFO_TEST_KEYCHAIN", config_dir() / "test.keychain-db" if FROZEN
                                    else ROOT / "pki/test.keychain-db"))
TEST_PFX = Path(os.environ.get("ZFO_TEST_PFX", config_dir() / "test.pfx" if FROZEN else ROOT / "pki/user.pfx"))


def _run_winsign(args, data=None):
    script = RESOURCES / "winsign.ps1"
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-File", str(script), *args], input=data, capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "winsign selhal")
    return result.stdout


# Every key duck-types cryptography's RSAPrivateKey.sign(data, padding, algorithm), which zfo_sign and pdf_sign use.
class WindowsKey:
    def __init__(self, sha1):
        self.sha1 = sha1

    def sign(self, data, _padding, algorithm):
        # Data goes through a file: PowerShell has no binary stdin.
        with tempfile.NamedTemporaryFile(delete=False) as data_file:
            data_file.write(data)
        try:
            algorithm_name = "sha1" if algorithm.name == "sha1" else "sha256"
            return base64.b64decode(_run_winsign(["sign", self.sha1, algorithm_name, data_file.name]).strip())
        finally:
            os.unlink(data_file.name)


def _windows_identities():
    found = json.loads(_run_winsign(["list"]) or b"[]")
    for item in found if isinstance(found, list) else [found]:
        item["cert"] = x509.load_der_x509_certificate(base64.b64decode(item.pop("certificate")))
        yield item


def _files_index():
    path = config_dir() / "certificates.json"
    return path, (json.loads(path.read_text(encoding="utf-8")) if path.exists() else [])


def add_pfx(data, password):
    # Keeps a copy of the still encrypted file; the password only checks it opens.
    key, cert, _ = pkcs12.load_key_and_certificates(data, password.encode() if password else None)
    if key is None or cert is None:
        raise ValueError("Soubor neobsahuje certifikát se soukromým klíčem")
    if not _can_sign(cert):
        raise ValueError("Certifikát není určen k podepisování")
    sha1 = hashlib.sha1(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    folder = config_dir() / "certificates"
    folder.mkdir(exist_ok=True)
    (folder / f"{sha1}.p12").write_bytes(data)
    index_path, index = _files_index()
    index = [entry for entry in index if entry["sha1"] != sha1] + [
        {"sha1": sha1, "subject": _common_name(cert), "not_after": f"{cert.not_valid_after_utc:%d. %m. %Y}"}]
    index_path.write_text(json.dumps(index), encoding="utf-8")
    return sha1


def remove_pfx(sha1):
    sha1 = _validated_sha1(sha1)
    index_path, index = _files_index()
    index_path.write_text(json.dumps([entry for entry in index if entry["sha1"] != sha1]), encoding="utf-8")
    (config_dir() / "certificates" / f"{sha1}.p12").unlink(missing_ok=True)


def _load_pfx(path, password):
    try:
        key, cert, _ = pkcs12.load_key_and_certificates(path.read_bytes(), password.encode() if password else None)
    except ValueError:
        raise ValueError("Nesprávné heslo k souboru s certifikátem") from None
    return key, cert


def _identity_entry(source, sha1, subject, cert, token=False, needs_password=False):
    return {"id": f"{source}:{sha1}", "subject": subject, "token": token, "source": source,
            "needs_password": needs_password, "not_after": f"{cert.not_valid_after_utc:%d. %m. %Y}", "cert": cert}


def _common_name(cert):
    names = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return names[0].value if names else cert.subject.rfc4514_string()


def _can_sign(cert):
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        return usage.digital_signature or usage.content_commitment
    except x509.ExtensionNotFound:
        return True


def signing_identities():
    found = []
    if IS_MAC:
        found += [_identity_entry("system", identity["sha1"], identity["subject"], identity["cert"], identity["token"]) for identity in keychain.signing_identities()]
        if TEST_KEYCHAIN.exists():
            found += [_identity_entry("test", identity["sha1"], identity["subject"], identity["cert"])
                      for identity in keychain.signing_identities(TEST_KEYCHAIN, TEST_PASSWORD)]
    else:
        if IS_WINDOWS:
            found += [_identity_entry("system", identity["sha1"], identity["subject"], identity["cert"], identity["token"]) for identity in _windows_identities()]
        if TEST_PFX.exists():
            _, cert = _load_pfx(TEST_PFX, TEST_PASSWORD)
            sha1 = hashlib.sha1(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
            found.append(_identity_entry("test", sha1, _common_name(cert), cert))
    found = [identity for identity in found if _can_sign(identity.pop("cert"))]
    # Added files are listed from the index: their certificate is usually encrypted together with the key.
    for entry in _files_index()[1]:
        if (config_dir() / "certificates" / f"{entry['sha1']}.p12").exists():
            found.append({"id": f"file:{entry['sha1']}", "subject": entry["subject"], "token": False, "source": "file",
                          "needs_password": True, "not_after": entry["not_after"]})
    return found


def _validated_sha1(value):
    # Ids come from requests and name files, so nothing but a SHA-1 thumbprint is accepted.
    if not re.fullmatch(r"[0-9a-f]{40}", value or ""):
        raise LookupError(f"neplatný certifikát {value!r}")
    return value


def load_identity(identity_id, password=None):
    source, sha1 = identity_id.split(":", 1)
    sha1 = _validated_sha1(sha1.lower())
    if source == "file":
        return _load_pfx(config_dir() / "certificates" / f"{sha1}.p12", password)
    if source == "test" and not IS_MAC:
        return _load_pfx(TEST_PFX, TEST_PASSWORD)
    if IS_MAC:
        return keychain.load_identity(sha1, *((TEST_KEYCHAIN, TEST_PASSWORD) if source == "test" else ()))
    if IS_WINDOWS:
        match = next((identity for identity in _windows_identities() if identity["sha1"] == sha1), None)
        if match is None:
            raise LookupError(f"certifikát {sha1} nebyl nalezen")
        return WindowsKey(sha1), match["cert"]
    raise LookupError(f"certifikát {identity_id} nebyl nalezen")


def _linux_system_roots():
    for bundle in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/ca-bundle.pem",
                   "/etc/ssl/cert.pem"):
        if Path(bundle).exists():
            return x509.load_pem_x509_certificates(Path(bundle).read_bytes())
    return []


def trust_verdicts(certificates):
    result = []
    for der, verdict in zip(certificates, _system_trust(certificates), strict=True):
        if verdict["trusted"]:
            result.append({**verdict, "source": "system", "unverifiable": False})
            continue
        listed = trustlist.check_certificate(der, config_dir() / "trustlists")
        # The system's own reason is in English and technical; the list's says what the reader can do about it.
        result.append({**listed, "source": "eu" if listed["trusted"] else "system"})
    return result


def stamp_trust_verdict(der, moment):
    return trustlist.check_stamp_certificate(der, moment, config_dir() / "trustlists")


def revocation_status(der):
    # May download the issuer's list, so never call it while rendering a page.
    return revocation.check_certificate_revocation(der, config_dir() / "trustlists", config_dir() / "crl")


def _system_trust(certificates):
    if not certificates:
        return []
    if IS_MAC:
        return keychain.trust_verdicts(certificates)
    if IS_WINDOWS:
        lines = "\n".join(base64.b64encode(der).decode() for der in certificates).encode()
        return [json.loads(line) for line in _run_winsign(["trust"], lines).splitlines() if line.strip()]
    import asyncio

    from asn1crypto import x509 as asn1_x509
    from pyhanko_certvalidator import CertificateValidator, ValidationContext
    roots = [asn1_x509.Certificate.load(root.public_bytes(serialization.Encoding.DER)) for root in _linux_system_roots()]
    others = [asn1_x509.Certificate.load(der) for der in certificates]
    result = []
    for cert in others:
        context = ValidationContext(trust_roots=roots, other_certs=others, allow_fetching=False)
        try:
            asyncio.run(CertificateValidator(cert, validation_context=context).async_validate_usage(set()))
            result.append({"trusted": True, "reason": ""})
        except Exception as error:  # noqa: BLE001 - any failure means "not trusted", with the reason shown
            result.append({"trusted": False, "reason": str(error).splitlines()[0][:200]})
    return result

