import base64
import binascii
import hashlib
import json
import re
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

RELEASE_PUBLIC_KEYS: tuple[str, ...] = (
    "Jadcas2Xo6tc4vxWrK4NMSrGP5uabqCNa/9BwytVXJw=",
    "Ylqu+RDshdNW+ls7+Sl0ryMoR+MRFV4i1hB73mAQKLM=",
)

PLATFORM_FILES = {
    "mac": "Blanketa-{version}-macos.zip",
    "windows": "Blanketa-{version}-setup.exe",
    "windows-portable": "Blanketa-{version}-windows-x64.zip",
    "linux": "blanketa-{version}-linux-amd64.tar.gz",
    "linux-deb": "blanketa_{version}_amd64.deb",
}
MANIFEST_NAME = "blanketa-{version}-update.json"
SIGNATURE_NAME = "blanketa-{version}-update.json.sig"

_SHA256 = re.compile(r"[0-9a-f]{64}")


def manifest(version: str, folder: Path) -> bytes:
    files = {}
    for platform, template in PLATFORM_FILES.items():
        path = Path(folder) / template.format(version=version)
        if not path.is_file():
            raise ValueError(f"Release file missing: {path.name}")
        files[path.name] = {"platform": platform, "sha256": file_digest(path)}
    return json.dumps({"version": version, "files": files}, sort_keys=True, separators=(",", ":")).encode()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sign(manifest: bytes, private_key_b64: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(_decode(private_key_b64, 32, "private key"))
    return base64.b64encode(key.sign(manifest)).decode()


def verified_manifest(manifest: bytes, signature_b64: str, keys=None) -> dict:
    if keys is None:
        keys = RELEASE_PUBLIC_KEYS
    signature = _decode(signature_b64, 64, "signature")
    if not any(_verifies(key, signature, manifest) for key in keys):
        raise ValueError("Manifest is not signed by a release key")
    return _checked(manifest)


def _decode(text: str, length: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError, AttributeError) as error:
        raise ValueError(f"Malformed {what}") from error
    if len(raw) != length:
        raise ValueError(f"Malformed {what}")
    return raw


def _verifies(key_b64: str, signature: bytes, manifest: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(_decode(key_b64, 32, "key")).verify(signature, manifest)
    except (InvalidSignature, ValueError):
        return False
    return True


def _checked(manifest: bytes) -> dict:
    try:
        data = json.loads(manifest)
    except ValueError as error:
        raise ValueError("Manifest is not JSON") from error
    if not isinstance(data, dict) or not isinstance(data.get("version"), str) or not isinstance(data.get("files"), dict):
        raise ValueError("Manifest has the wrong shape")
    for name, entry in data["files"].items():
        if (not isinstance(entry, dict) or entry.get("platform") not in PLATFORM_FILES
                or not isinstance(entry.get("sha256"), str) or not _SHA256.fullmatch(entry["sha256"])):
            raise ValueError(f"Manifest entry is invalid: {name}")
    return data
