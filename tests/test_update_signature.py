import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from support import ROOT

import update_signature

VERSION = "0.6.0"
RAW = serialization.Encoding.Raw
PRIVATE_RAW = serialization.PrivateFormat.Raw
PUBLIC_RAW = serialization.PublicFormat.Raw


def new_key():
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(key.private_bytes(RAW, PRIVATE_RAW, serialization.NoEncryption())).decode()
    public = base64.b64encode(key.public_key().public_bytes(RAW, PUBLIC_RAW)).decode()
    return private, public


def release_folder(folder, version=VERSION):
    for platform, template in update_signature.PLATFORM_FILES.items():
        (Path(folder) / template.format(version=version)).write_bytes(platform.encode() * 10)
    (Path(folder) / "junk.txt").write_text("not a release file")


class SignedManifestTest(unittest.TestCase):
    def setUp(self):
        self.private, self.public = new_key()
        self.manifest = json.dumps({"version": VERSION, "files": {}}).encode()
        self.signature = update_signature.sign(self.manifest, self.private)

    def test_signed_manifest_verifies(self):
        data = update_signature.verified_manifest(self.manifest, self.signature, keys=(self.public,))
        self.assertEqual(data["version"], VERSION)

    def test_any_listed_key_is_accepted(self):
        _, other = new_key()
        update_signature.verified_manifest(self.manifest, self.signature, keys=(other, self.public))

    def test_changed_byte_is_refused(self):
        changed = self.manifest.replace(b"0.6.0", b"0.6.1")
        with self.assertRaises(ValueError):
            update_signature.verified_manifest(changed, self.signature, keys=(self.public,))

    def test_unknown_key_is_refused(self):
        _, other = new_key()
        with self.assertRaises(ValueError):
            update_signature.verified_manifest(self.manifest, self.signature, keys=(other,))

    def test_no_keys_refuses_everything(self):
        with self.assertRaises(ValueError):
            update_signature.verified_manifest(self.manifest, self.signature, keys=())

    def test_default_keys_are_read_from_the_module_when_called(self):
        with mock.patch.object(update_signature, "RELEASE_PUBLIC_KEYS", (self.public,)):
            update_signature.verified_manifest(self.manifest, self.signature)
        with mock.patch.object(update_signature, "RELEASE_PUBLIC_KEYS", ()):
            with self.assertRaises(ValueError):
                update_signature.verified_manifest(self.manifest, self.signature)

    def test_malformed_signature_is_refused(self):
        for signature in ("", "not base64!!", base64.b64encode(b"short").decode()):
            with self.assertRaises(ValueError):
                update_signature.verified_manifest(self.manifest, signature, keys=(self.public,))

    def test_signed_garbage_is_refused(self):
        for body in (b"not json", b"[]", b'{"version":"0.6.0"}', b'{"version":1,"files":{}}',
                     b'{"version":"0.6.0","files":{"a":{"platform":"amiga","sha256":"' + b"0" * 64 + b'"}}}',
                     b'{"version":"0.6.0","files":{"a":{"platform":"mac","sha256":"xyz"}}}'):
            with self.assertRaises(ValueError, msg=body):
                update_signature.verified_manifest(body, update_signature.sign(body, self.private),
                                                   keys=(self.public,))


class ManifestTest(unittest.TestCase):
    def test_manifest_lists_only_release_files(self):
        with tempfile.TemporaryDirectory() as folder:
            release_folder(folder)
            data = json.loads(update_signature.manifest(VERSION, Path(folder)))
            self.assertEqual(data["version"], VERSION)
            self.assertEqual(len(data["files"]), 5)
            for platform, template in update_signature.PLATFORM_FILES.items():
                name = template.format(version=VERSION)
                content = (Path(folder) / name).read_bytes()
                self.assertEqual(data["files"][name],
                                 {"platform": platform, "sha256": hashlib.sha256(content).hexdigest()})

    def test_manifest_is_canonical(self):
        with tempfile.TemporaryDirectory() as folder:
            release_folder(folder)
            body = update_signature.manifest(VERSION, Path(folder))
            data = json.loads(body)
            self.assertEqual(body, json.dumps(data, sort_keys=True, separators=(",", ":")).encode())

    def test_manifest_without_a_platform_file_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            release_folder(folder)
            (Path(folder) / update_signature.PLATFORM_FILES["linux-deb"].format(version=VERSION)).unlink()
            with self.assertRaises(ValueError):
                update_signature.manifest(VERSION, Path(folder))


class SignReleaseToolTest(unittest.TestCase):
    def run_tool(self, folder, secret, listed_keys=None):
        env = {key: value for key, value in os.environ.items()
               if key not in ("UPDATE_SIGNING_KEY", "BLANKETA_RELEASE_PUBLIC_KEYS")}
        env["UPDATE_SIGNING_KEY"] = secret
        if listed_keys is not None:
            env["BLANKETA_RELEASE_PUBLIC_KEYS"] = ",".join(listed_keys)
        return subprocess.run([sys.executable, "-I", str(ROOT / "tools/sign_release.py"), str(folder), VERSION],
                              env=env, capture_output=True, text=True)

    def written(self, folder):
        return sorted(p.name for p in Path(folder).iterdir() if "update" in p.name)

    def test_sign_release_tool_signs_and_verifies(self):
        private, public = new_key()
        with tempfile.TemporaryDirectory() as folder:
            release_folder(folder)
            result = self.run_tool(folder, private, [public])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.written(folder), [f"blanketa-{VERSION}-update.json",
                                                    f"blanketa-{VERSION}-update.json.sig"])
            body = (Path(folder) / f"blanketa-{VERSION}-update.json").read_bytes()
            signature = (Path(folder) / f"blanketa-{VERSION}-update.json.sig").read_text().strip()
            update_signature.verified_manifest(body, signature, keys=(public,))

    def test_sign_release_tool_fails_without_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            release_folder(folder)
            result = self.run_tool(folder, "")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.written(folder), [])

    def test_sign_release_tool_fails_when_key_is_not_listed(self):
        private, _ = new_key()
        _, other = new_key()
        with tempfile.TemporaryDirectory() as folder:
            release_folder(folder)
            result = self.run_tool(folder, private, [other])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.written(folder), [])

    def test_sign_release_tool_fails_with_the_built_in_empty_key_list(self):
        private, _ = new_key()
        with tempfile.TemporaryDirectory() as folder:
            release_folder(folder)
            result = self.run_tool(folder, private)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.written(folder), [])

    def test_key_never_printed(self):
        private, public = new_key()
        _, other = new_key()
        for listed in ([public], [other]):
            with tempfile.TemporaryDirectory() as folder:
                release_folder(folder)
                result = self.run_tool(folder, private, listed)
                self.assertNotIn(private, result.stdout + result.stderr)

    def test_release_keys_tool_prints_a_matching_pair_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, "-I", str(ROOT / "tools/release_keys.py")],
                                    capture_output=True, text=True, cwd=folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(folder).iterdir()), [])
        tokens = [word for word in result.stdout.split() if len(word) == 44 and word.endswith("=")]
        self.assertEqual(len(tokens), 2)
        private, public = tokens
        body = json.dumps({"version": VERSION, "files": {}}).encode()
        update_signature.verified_manifest(body, update_signature.sign(body, private), keys=(public,))


class ReleaseKeysTest(unittest.TestCase):
    def test_app_carries_a_main_and_a_spare_release_key(self):
        keys = update_signature.RELEASE_PUBLIC_KEYS
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(set(keys)), 2)
        for key in keys:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True))

    def test_a_signature_by_an_unlisted_key_is_refused_by_default(self):
        private, _ = new_key()
        body = json.dumps({"version": VERSION, "files": {}}).encode()
        with self.assertRaises(ValueError):
            update_signature.verified_manifest(body, update_signature.sign(body, private))


if __name__ == "__main__":
    unittest.main()
