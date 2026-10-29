"""Signing keys held by macOS (keychain or CryptoTokenKit token), used through macsign."""
import base64
import json
import os
import subprocess
from pathlib import Path

from cryptography import x509

MACSIGN = Path(os.environ.get("ZFO_MACSIGN", Path(__file__).resolve().parent / "macsign/macsign"))


def _run(args, keychain=None, data=None, password=None):
    env = dict(os.environ)
    if keychain:
        args = args + ["--keychain", str(Path(keychain).resolve())]
        if password is not None:
            env["ZFO_KEYCHAIN_PASSWORD"] = password
    result = subprocess.run([str(MACSIGN), *args], input=data, capture_output=True, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.decode().strip())
    return result.stdout


def identities(keychain=None, password=None):
    found = json.loads(_run(["list"], keychain, password=password))
    for item in found:
        item["cert"] = x509.load_der_x509_certificate(base64.b64decode(item.pop("certificate")))
    return found


def trust(certificates):
    """macOS trust verdict ({"trusted": bool, "reason": str}) for each DER certificate."""
    lines = "\n".join(base64.b64encode(der).decode() for der in certificates).encode()
    return [json.loads(line) for line in _run(["trust"], data=lines).splitlines()]


class KeychainKey:
    """Duck-types cryptography's RSAPrivateKey.sign(data, padding, algorithm)."""

    def __init__(self, sha1, keychain=None, password=None):
        self.sha1, self.keychain, self.password = sha1.lower(), keychain, password

    def sign(self, data, _padding, algorithm):
        alg = "sha1" if algorithm.name == "sha1" else "sha256"
        return base64.b64decode(_run(["sign", "--cert", self.sha1, "--alg", alg], self.keychain, data, self.password))


def load(sha1, keychain=None, password=None):
    match = next((i for i in identities(keychain, password) if i["sha1"] == sha1.lower()), None)
    if match is None:
        raise LookupError(f"certificate {sha1} not found")
    return KeychainKey(sha1, keychain, password), match["cert"]
