import base64
import email.message
import hashlib
import http.client
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import support  # noqa: F401
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app
import certstore
import update_check
import update_install
import update_signature


def running(executable, *, mac=False, windows=False, writable=True, env=None):
    patches = [
        mock.patch.object(update_install.sys, "frozen", True, create=True),
        mock.patch.object(update_install.sys, "executable", str(executable)),
        mock.patch.object(update_install.certstore, "IS_MAC", mac),
        mock.patch.object(update_install.certstore, "IS_WINDOWS", windows),
        mock.patch.object(update_install.os, "access", return_value=writable),
        mock.patch.dict(os.environ, env or {}),
    ]
    for patcher in patches:
        patcher.start()
    return patches


class WithPatches(unittest.TestCase):
    def run_as(self, *args, **kwargs):
        for patcher in running(*args, **kwargs):
            self.addCleanup(patcher.stop)


class UpdatesFolder(unittest.TestCase):
    def test_lives_in_the_config_folder(self):
        self.assertEqual(update_install.updates_dir(), certstore.config_dir() / "updates")


class NotFrozen(unittest.TestCase):
    def test_source_run_is_not_installable(self):
        self.assertIsNone(update_install.target())
        self.assertIsNone(update_install.not_replaceable_reason())


class Mac(WithPatches):
    def app_executable(self, folder):
        return Path(folder) / "Blanketa.app/Contents/Resources/server/blanketa-server"

    def test_app_in_applications_is_installable(self):
        self.run_as(self.app_executable("/Applications"), mac=True)
        self.assertEqual(
            update_install.target(),
            {"kind": "mac-app", "app": Path("/Applications/Blanketa.app"), "all_users": False},
        )
        self.assertIsNone(update_install.not_replaceable_reason())

    def test_dmg_and_translocated_app_give_the_move_reason(self):
        for folder in ("/Volumes/Blanketa", "/private/var/folders/x/AppTranslocation/ABC/d"):
            with self.subTest(folder=folder):
                self.run_as(self.app_executable(folder), mac=True)
                self.assertIsNone(update_install.target())
                self.assertEqual(update_install.not_replaceable_reason(), "move-to-applications")

    def test_shallow_path_is_not_replaceable(self):
        self.run_as("/blanketa", mac=True)
        self.assertIsNone(update_install.target())
        self.assertEqual(update_install.not_replaceable_reason(), "move-to-applications")

    def test_unwritable_parent_gives_the_move_reason(self):
        self.run_as(self.app_executable("/Applications"), mac=True, writable=False)
        self.assertIsNone(update_install.target())
        self.assertEqual(update_install.not_replaceable_reason(), "move-to-applications")


class Windows(WithPatches):
    def test_setup_install_per_user(self):
        self.run_as("C:/Users/x/AppData/Local/Programs/Blanketa/Blanketa.exe", windows=True, env={"ProgramFiles": "/pf"})
        with mock.patch.object(update_check, "portable_windows", return_value=False):
            found = update_install.target()
        self.assertEqual(found["kind"], "windows-setup")
        self.assertFalse(found["all_users"])

    def test_setup_install_in_program_files_is_for_all_users(self):
        self.run_as("/pf/Blanketa/Blanketa.exe", windows=True, env={"ProgramFiles": "/pf"})
        with mock.patch.object(update_check, "portable_windows", return_value=False):
            found = update_install.target()
        self.assertEqual((found["kind"], found["all_users"], found["app"]), ("windows-setup", True, Path("/pf/Blanketa")))

    def test_portable_folder(self):
        self.run_as("/data/Blanketa/Blanketa.exe", windows=True, env={"ProgramFiles": "/pf"})
        with mock.patch.object(update_check, "portable_windows", return_value=True):
            found = update_install.target()
        self.assertEqual(found, {"kind": "windows-portable", "app": Path("/data/Blanketa"), "all_users": False})
        self.assertIsNone(update_install.not_replaceable_reason())


class Linux(WithPatches):
    def test_folder_in_the_data_home_is_installable(self):
        with tempfile.TemporaryDirectory() as home:
            self.run_as(Path(home) / "blanketa/blanketa", env={"XDG_DATA_HOME": home})
            self.assertEqual(
                update_install.target(),
                {"kind": "linux-folder", "app": Path(home) / "blanketa", "all_users": False},
            )

    def test_default_data_home(self):
        self.run_as(Path.home() / ".local/share/blanketa/blanketa")
        with mock.patch.dict(os.environ):
            os.environ.pop("XDG_DATA_HOME", None)
            self.assertEqual(update_install.target()["kind"], "linux-folder")

    def test_deb_in_opt_is_not_installable(self):
        self.run_as("/opt/blanketa/blanketa", env={"XDG_DATA_HOME": "/home/x/.local/share"})
        self.assertIsNone(update_install.target())

    def test_unpacked_tar_folder_run_directly_is_not_installable(self):
        self.run_as("/home/x/Downloads/blanketa/blanketa", env={"XDG_DATA_HOME": "/home/x/.local/share"})
        self.assertIsNone(update_install.target())

    def test_unwritable_folder_is_not_installable(self):
        self.run_as("/home/x/.local/share/blanketa/blanketa", env={"XDG_DATA_HOME": "/home/x/.local/share"}, writable=False)
        self.assertIsNone(update_install.target())


RELEASE_DOWNLOADS = "https://github.com/arosis/blanketa/releases/download/v{version}/"
STAGED_APP_FILES = {
    "mac-app": "Blanketa.app/Contents/MacOS/Blanketa",
    "windows-portable": "Blanketa/Blanketa.exe",
    "linux-folder": "Blanketa/Blanketa",
}


def can_make_symlinks():
    with tempfile.TemporaryDirectory() as folder:
        try:
            (Path(folder) / "link").symlink_to(folder)
        except OSError:
            return False
    return True


needs_symlinks = unittest.skipUnless(can_make_symlinks(), "needs the right to make symbolic links (Windows developer mode)")


def zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            if isinstance(content, tuple):
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, content[1])
            else:
                archive.writestr(name, content)
    return buffer.getvalue()


def tar_bytes(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            if isinstance(content, tuple):
                info.type = tarfile.LNKTYPE if content[0] == "hardlink" else tarfile.SYMTYPE
                info.linkname = content[1]
                archive.addfile(info)
            else:
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def release_files(version, archives=None):
    archives = archives or {}
    return {
        f"Blanketa-{version}-macos.zip": zip_bytes(archives.get("mac", {STAGED_APP_FILES["mac-app"]: b"mac"})),
        f"Blanketa-{version}-setup.exe": b"setup",
        f"Blanketa-{version}-windows-x64.zip": zip_bytes(archives.get("windows-portable", {STAGED_APP_FILES["windows-portable"]: b"exe"})),
        f"blanketa-{version}-linux-amd64.tar.gz": tar_bytes(archives.get("linux", {STAGED_APP_FILES["linux-folder"]: b"elf"})),
        f"blanketa_{version}_amd64.deb": b"deb",
    }


class Answer(io.BytesIO):
    def __init__(self, body, declared_length=None, gate=None):
        super().__init__(body)
        self.headers = {} if declared_length is None else {"Content-Length": str(declared_length)}
        self.gate = gate
        self.bytes_read = 0

    def read(self, size=-1):
        if self.gate is not None and self.tell() > 0:
            self.gate["reached"].set()
            self.gate["release"].wait(10)
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


class FakeRelease:
    def __init__(self):
        self.files = {}
        self.lengths = {}
        self.gates = {}
        self.requests = []
        self.answers = {}

    def open(self, request, timeout=None):
        url = request.full_url
        self.requests.append(url)
        if url not in self.files:
            raise urllib.error.URLError("Not Found")
        body = self.files[url]
        self.answers[url] = Answer(body, self.lengths.get(url, len(body)), self.gates.get(url))
        return self.answers[url]


class Downloads(unittest.TestCase):
    kind = "linux-folder"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.config = self.folder / "config"
        self.config.mkdir()
        self.apps = self.folder / "apps"
        self.apps.mkdir()
        self.private_key = Ed25519PrivateKey.generate()
        public_key = base64.b64encode(self.private_key.public_key().public_bytes_raw()).decode()
        self.release = FakeRelease()
        for patcher in (
            mock.patch.object(certstore, "config_dir", return_value=self.config),
            mock.patch.object(update_signature, "RELEASE_PUBLIC_KEYS", (public_key,)),
            mock.patch.object(update_install, "_update_opener", return_value=self.release),
            mock.patch.object(update_install, "target", side_effect=lambda: self.target()),
            mock.patch.dict(update_install._state, update_install.IDLE),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.wait)

    def target(self):
        app = self.apps / ("Blanketa.app" if self.kind == "mac-app" else "blanketa")
        return {"kind": self.kind, "app": app, "all_users": False}

    def publish(self, version="0.5.0", manifest_version=None, archives=None, signing_key=None):
        files = release_files(version, archives)
        manifest_version = manifest_version or version
        built = self.folder / f"built-{version}-{manifest_version}"
        built.mkdir(exist_ok=True)
        for name, content in release_files(manifest_version, archives).items():
            (built / name).write_bytes(content)
        manifest = update_signature.manifest(manifest_version, built)
        key = signing_key or self.private_key
        private_b64 = base64.b64encode(key.private_bytes_raw()).decode()
        served = {**files, f"blanketa-{version}-update.json": manifest,
                  f"blanketa-{version}-update.json.sig": update_signature.sign(manifest, private_b64).encode()}
        prefix = RELEASE_DOWNLOADS.format(version=version)
        self.release.files.update({prefix + name: content for name, content in served.items()})
        (self.config / update_check.SETTINGS_FILE).write_text(json.dumps(
            {"latest": {"version": version, "assets": {name: prefix + name for name in served}}}), encoding="utf-8")
        return prefix

    def archive_url(self, version="0.5.0"):
        name = update_signature.PLATFORM_FILES[update_install.KIND_FILES[self.kind]].format(version=version)
        return RELEASE_DOWNLOADS.format(version=version) + name

    def wait(self):
        deadline = time.monotonic() + 10
        while update_install.progress("0.4.0")["state"] == "downloading" and time.monotonic() < deadline:
            time.sleep(0.01)
        return update_install.progress("0.4.0")

    def download(self, current="0.4.0"):
        update_install.start_download(current)
        return self.wait()

    def leftovers(self):
        updates = update_install.updates_dir()
        found = [path for path in updates.rglob("*") if path.is_file()] if updates.exists() else []
        found += [path for path in self.apps.iterdir() if path.name.lower().startswith(".blanketa-update")]
        return found

    def assert_refused(self, error):
        self.assertEqual(self.download(), {"state": "failed", "version": "0.5.0", "percent": None, "error": error})
        self.assertIsNone(update_install.ready("0.4.0"))
        self.assertEqual(self.leftovers(), [])


class VerifiedDownload(Downloads):
    def test_verified_download_is_staged_and_ready(self):
        cases = {
            "linux-folder": ".blanketa-update-0.5.0/Blanketa",
            "windows-portable": ".blanketa-update-0.5.0/Blanketa",
            "windows-setup": None,
        }
        if shutil.which("ditto"):
            cases["mac-app"] = ".Blanketa-update-0.5.0/Blanketa.app"
        for kind, staged in cases.items():
            with self.subTest(kind=kind):
                self.kind = kind
                self.publish()
                self.assertEqual(self.download()["state"], "ready")
                ready = update_install.ready("0.4.0")
                expected_staged = (update_install.updates_dir() / "0.5.0/Blanketa-0.5.0-setup.exe" if staged is None
                                   else self.apps / staged)
                expected = {"version": "0.5.0", "kind": kind, "app": str(self.target()["app"]),
                            "staged": str(expected_staged), "all_users": False}
                if staged is None:
                    expected["sha256"] = hashlib.sha256(b"setup").hexdigest()
                self.assertEqual(ready, expected)
                self.assertTrue(expected_staged.exists())
                if kind in STAGED_APP_FILES:
                    self.assertTrue((expected_staged.parent / STAGED_APP_FILES[kind]).is_file())
                self.assertEqual(list(update_install.updates_dir().rglob("*.part")), [])
                self.assertEqual(update_install.progress("0.4.0"),
                                 {"state": "ready", "version": "0.5.0", "percent": 100, "error": None})
                (update_install.updates_dir() / "ready.json").unlink()
                self.assertEqual(update_install.progress("0.4.0"), update_install.IDLE)

    def test_requests_only_the_release_files_for_this_kind(self):
        prefix = self.publish()
        self.download()
        self.assertEqual(self.release.requests, [prefix + "blanketa-0.5.0-update.json", prefix + "blanketa-0.5.0-update.json.sig",
                                                 prefix + "blanketa-0.5.0-linux-amd64.tar.gz"])

    def test_ready_update_of_the_same_version_is_not_downloaded_again(self):
        self.publish()
        self.download()
        self.release.requests.clear()
        self.assertEqual(update_install.start_download("0.4.0")["state"], "ready")
        self.assertEqual(self.release.requests, [])

    def test_newer_release_replaces_an_older_ready_one(self):
        self.publish("0.5.0")
        self.download()
        self.publish("0.6.0")
        update_install.start_download("0.4.0")
        self.assertEqual(self.wait()["version"], "0.6.0")
        self.assertFalse((update_install.updates_dir() / "0.5.0").exists())
        self.assertFalse((self.apps / ".blanketa-update-0.5.0").exists())
        self.assertEqual(update_install.ready("0.4.0")["version"], "0.6.0")


class Refused(Downloads):
    def test_wrong_digest_is_refused_and_cleaned(self):
        self.publish()
        self.release.files[self.archive_url()] = tar_bytes({"Blanketa/Blanketa": b"other"})
        self.assert_refused("verify")

    def test_bad_signature_is_refused(self):
        self.publish(signing_key=Ed25519PrivateKey.generate())
        self.assert_refused("verify")
        self.assertEqual(len(self.release.requests), 2)

    def test_damaged_signature_or_manifest_is_refused(self):
        prefix = self.publish()
        for name, content in (("blanketa-0.5.0-update.json.sig", b"\xff\xfe"), ("blanketa-0.5.0-update.json.sig", b"nesmysl"),
                              ("blanketa-0.5.0-update.json", b"{}"), ("blanketa-0.5.0-update.json", b"x" * 2_000_000)):
            with self.subTest(name=name, content=content[:10]):
                self.publish()
                self.release.files[prefix + name] = content
                self.assert_refused("verify")

    def test_older_or_same_version_manifest_is_refused(self):
        for manifest_version in ("0.4.0", "0.3.0", "0.6.0"):
            with self.subTest(manifest_version=manifest_version):
                self.publish("0.5.0", manifest_version=manifest_version)
                self.assert_refused("verify")
                self.assertNotIn(self.archive_url(), self.release.requests)

    def test_release_not_newer_than_the_running_version_is_not_downloaded(self):
        self.publish("0.5.0")
        for current in ("0.5.0", "0.6.0"):
            with self.subTest(current=current):
                self.assertEqual(self.download(current), update_install.IDLE)
        self.assertEqual(self.release.requests, [])

    def test_file_listed_under_another_platform_is_refused(self):
        version = "0.5.0"
        prefix = self.publish(version)
        manifest = json.loads(self.release.files[prefix + f"blanketa-{version}-update.json"])
        entries = manifest["files"]
        linux, deb = f"blanketa-{version}-linux-amd64.tar.gz", f"blanketa_{version}_amd64.deb"
        entries[linux]["platform"], entries[deb]["platform"] = "linux-deb", "linux"
        self.serve_signed_manifest(prefix, version, manifest)
        self.assert_refused("verify")

    def test_manifest_naming_another_version_is_refused(self):
        version = "0.5.0"
        prefix = self.publish(version)
        manifest = json.loads(self.release.files[prefix + f"blanketa-{version}-update.json"])
        for other in ("0.4.0", "0.6.0", "v0.5.0", "0.5.0.1"):
            with self.subTest(other=other):
                self.serve_signed_manifest(prefix, version, {**manifest, "version": other})
                self.assert_refused("verify")
                self.assertNotIn(self.archive_url(), self.release.requests)

    def test_file_missing_from_the_manifest_is_refused(self):
        version = "0.5.0"
        prefix = self.publish(version)
        manifest = json.loads(self.release.files[prefix + f"blanketa-{version}-update.json"])
        del manifest["files"][f"blanketa-{version}-linux-amd64.tar.gz"]
        self.serve_signed_manifest(prefix, version, manifest)
        self.assert_refused("verify")

    def serve_signed_manifest(self, prefix, version, manifest):
        content = json.dumps(manifest).encode()
        private_b64 = base64.b64encode(self.private_key.private_bytes_raw()).decode()
        self.release.files[prefix + f"blanketa-{version}-update.json"] = content
        self.release.files[prefix + f"blanketa-{version}-update.json.sig"] = update_signature.sign(content, private_b64).encode()

    def test_truncated_download_fails_cleanly(self):
        self.publish()
        url = self.archive_url()
        self.release.lengths[url] = len(self.release.files[url]) + 100
        self.assert_refused("download")

    def test_impossible_declared_length_is_refused_before_reading(self):
        for length in (-1, update_install.MAX_DOWNLOAD_BYTES + 1):
            with self.subTest(length=length):
                self.publish()
                self.release.lengths[self.archive_url()] = length
                self.assert_refused("download")
                self.assertEqual(self.release.answers[self.archive_url()].bytes_read, 0)

    def test_bytes_changed_on_disk_after_download_are_refused(self):
        self.publish()
        report_percent = update_install._report_percent

        def tamper(received, length):
            for partial in update_install.updates_dir().rglob("*.part"):
                with partial.open("ab") as stream:
                    stream.write(b"x" * 100_000)
            report_percent(received, length)

        with mock.patch.object(update_install, "_report_percent", tamper):
            self.assert_refused("verify")

    def test_unexpected_failure_never_leaves_the_state_downloading(self):
        self.publish()
        with mock.patch.object(update_install, "_outcome", side_effect=RuntimeError):
            self.assertEqual(self.download(), {"state": "failed", "version": "0.5.0", "percent": None, "error": "download"})

    def test_missing_file_or_network_failure_fails_cleanly(self):
        self.publish()
        del self.release.files[self.archive_url()]
        self.assert_refused("download")

    def test_foreign_url_in_the_stored_release_is_never_requested(self):
        prefix = self.publish()
        settings = self.config / update_check.SETTINGS_FILE
        stored = json.loads(settings.read_text(encoding="utf-8"))
        stored["latest"]["assets"]["blanketa-0.5.0-update.json"] = "https://example.com/blanketa-0.5.0-update.json"
        settings.write_text(json.dumps(stored), encoding="utf-8")
        self.assertEqual(self.download()["state"], "failed")
        self.assertEqual(self.release.requests, [])
        self.assertTrue(prefix)

    def test_unwritable_updates_folder_fails_without_downloading(self):
        self.publish()
        (self.config / "updates").write_text("")
        self.assertEqual(self.download(), {"state": "failed", "version": "0.5.0", "percent": None, "error": "download"})
        self.assertEqual(self.release.requests, [])

    def test_copy_that_cannot_install_downloads_nothing(self):
        self.publish()
        with mock.patch.object(update_install, "target", return_value=None):
            self.assertEqual(self.download()["state"], "failed")
        self.assertEqual(self.release.requests, [])


class EscapingArchive(Downloads):
    def test_archive_escaping_its_folder_is_refused(self):
        app_file = {"Blanketa/Blanketa": b"elf"}
        tar_cases = {
            "parent": {**app_file, "../x": b"x"},
            "nested parent": {**app_file, "Blanketa/../../x": b"x"},
            "absolute": {**app_file, "/tmp/x": b"x"},
            "symlink out": {**app_file, "Blanketa/link": ("link", "../../outside")},
            "absolute symlink": {**app_file, "Blanketa/link": ("link", "/etc")},
            "hardlink out": {**app_file, "Blanketa/link": ("hardlink", "../../outside")},
        }
        zip_file = {"Blanketa/Blanketa.exe": b"exe"}
        zip_cases = {
            "parent": {**zip_file, "../x": b"x"},
            "backslash parent": {**zip_file, "..\\x": b"x"},
            "absolute": {**zip_file, "/tmp/x": b"x"},
            "drive": {**zip_file, "C:/x": b"x"},
            "drive relative": {**zip_file, "C:x": b"x"},
            "network share": {**zip_file, "\\\\server\\share\\x": b"x"},
            "symlink out": {**zip_file, "Blanketa/link": ("link", "../../outside")},
        }
        for kind, platform, cases in (("linux-folder", "linux", tar_cases), ("windows-portable", "windows-portable", zip_cases)):
            for case, entries in cases.items():
                with self.subTest(kind=kind, case=case):
                    self.kind = kind
                    self.publish(archives={platform: entries})
                    self.assert_refused("verify")
                    self.assertFalse((self.apps / "x").exists())
                    self.assertFalse((self.folder / "x").exists())

    @unittest.skipUnless(shutil.which("ditto"), "needs ditto (macOS)")
    def test_mac_archive_with_a_link_out_is_refused(self):
        self.kind = "mac-app"
        for entries in ({STAGED_APP_FILES["mac-app"]: b"mac", "Blanketa.app/link": ("link", "../../outside")},
                        {STAGED_APP_FILES["mac-app"]: b"mac", "../x": b"x"}):
            with self.subTest(entries=sorted(entries)):
                self.publish(archives={"mac": entries})
                self.assert_refused("verify")

    def test_archive_without_the_app_is_refused(self):
        self.publish(archives={"linux": {"Blanketa/README": b"x"}})
        self.assert_refused("verify")

    @needs_symlinks
    def test_link_out_left_by_the_unpacker_is_refused(self):
        def unpack_with_a_link_out(kind, archive, staging):
            (staging / "Blanketa").mkdir()
            (staging / "Blanketa/Blanketa").write_bytes(b"elf")
            (staging / "Blanketa/out").symlink_to(self.folder)

        self.publish()
        with mock.patch.object(update_install, "_unpack", unpack_with_a_link_out):
            self.assert_refused("verify")

    @needs_symlinks
    def test_link_out_of_the_staged_tree_is_found(self):
        staged = self.folder / "staged"
        (staged / "Blanketa.app/Contents").mkdir(parents=True)
        (staged / "Blanketa.app/Contents/Current").symlink_to("..")
        update_install.refuse_links_out_of(staged)
        (staged / "Blanketa.app/Contents/out").symlink_to("../../..")
        with self.assertRaises(ValueError):
            update_install.refuse_links_out_of(staged)


def write_ready(updates, apps, version="0.5.0", staged=None):
    staged = staged or apps / ".blanketa-update-0.5.0/Blanketa"
    staged.mkdir(parents=True, exist_ok=True)
    record = {"version": version, "kind": "linux-folder", "app": str(apps / "blanketa"), "staged": str(staged), "all_users": False}
    (updates / "ready.json").write_text(json.dumps(record))
    return record


class ReadyRecord(Downloads):
    def test_ready_update_is_read_back(self):
        updates = update_install.updates_dir()
        updates.mkdir(parents=True)
        record = write_ready(updates, self.apps)
        self.assertEqual(update_install.ready("0.4.0"), record)

    def test_stale_ready_record_is_ignored_and_deleted(self):
        updates = update_install.updates_dir()
        updates.mkdir(parents=True)
        for current in ("0.5.0", "0.6.0", "x"):
            with self.subTest(current=current):
                write_ready(updates, self.apps)
                self.assertIsNone(update_install.ready(current))
                self.assertFalse((updates / "ready.json").exists())
        write_ready(updates, self.apps)
        shutil.rmtree(self.apps / ".blanketa-update-0.5.0")
        self.assertIsNone(update_install.ready("0.4.0"))
        self.assertFalse((updates / "ready.json").exists())

    def test_setup_record_needs_its_digest(self):
        updates = update_install.updates_dir()
        updates.mkdir(parents=True)
        setup = updates / "Blanketa-0.5.0-setup.exe"
        setup.write_bytes(b"setup")
        record = {"version": "0.5.0", "kind": "windows-setup", "app": "/pf/Blanketa", "staged": str(setup), "all_users": True}
        (updates / "ready.json").write_text(json.dumps(record))
        self.assertIsNone(update_install.ready("0.4.0"))
        record["sha256"] = hashlib.sha256(b"setup").hexdigest()
        (updates / "ready.json").write_text(json.dumps(record))
        self.assertEqual(update_install.ready("0.4.0"), record)


class AssetRedirects(unittest.TestCase):
    RELEASE_URL = "https://github.com/arosis/blanketa/releases/download/v0.5.0/blanketa-0.5.0-linux-amd64.tar.gz"

    def redirect(self, request, location):
        handler = next(h for h in update_install._update_opener().handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
        followed = []
        handler.parent = mock.Mock(open=lambda new, timeout=None: followed.append(new) or new)
        headers = email.message.Message()
        headers["Location"] = location
        request.timeout = None
        handler.http_error_302(request, io.BytesIO(), 302, "Found", headers)
        return followed

    def test_redirect_to_the_release_asset_hosts_is_followed(self):
        for host in ("release-assets.githubusercontent.com", "objects.githubusercontent.com"):
            with self.subTest(host=host):
                followed = self.redirect(urllib.request.Request(self.RELEASE_URL), f"https://{host}/github/1?sig=x")
                self.assertEqual([request.full_url for request in followed], [f"https://{host}/github/1?sig=x"])

    def test_other_redirects_are_refused(self):
        for location in ("http://objects.githubusercontent.com/x", "https://example.com/x",
                         "https://objects.githubusercontent.com.example.com/x", "https://user@objects.githubusercontent.com/x",
                         "https://objects.githubusercontent.com:8443/x", "/arosis/other/x"):
            with self.subTest(location=location):
                with self.assertRaises(urllib.error.HTTPError):
                    self.redirect(urllib.request.Request(self.RELEASE_URL), location)

    def test_fourth_hop_is_refused(self):
        request = urllib.request.Request(self.RELEASE_URL)
        for hop in range(update_install.MAX_REDIRECTS):
            request = self.redirect(request, f"https://objects.githubusercontent.com/{hop}")[0]
        with self.assertRaises(urllib.error.HTTPError):
            self.redirect(request, "https://objects.githubusercontent.com/last")

    def test_other_services_still_refuse_redirects(self):
        handlers = update_install.public_services.opener().handlers
        self.assertFalse(any(isinstance(h, update_install._ReleaseAssetRedirects) for h in handlers))


class OneDownloadAtATime(Downloads):
    def hold(self, url):
        gate = {"reached": threading.Event(), "release": threading.Event()}
        self.release.gates[url] = gate
        self.addCleanup(gate["release"].set)
        return gate

    def test_second_download_joins_the_first(self):
        self.publish()
        gate = self.hold(self.archive_url())
        first = update_install.start_download("0.4.0")
        self.assertTrue(gate["reached"].wait(10))
        second = update_install.start_download("0.4.0")
        self.assertEqual((first["state"], second["state"], second["version"]), ("downloading", "downloading", "0.5.0"))
        gate["release"].set()
        self.assertEqual(self.wait()["state"], "ready")
        self.assertEqual(self.release.requests.count(self.archive_url()), 1)

    def test_download_held_by_another_live_process_is_reported(self):
        self.publish()
        updates = update_install.updates_dir()
        updates.mkdir(parents=True)
        (updates / "download.lock").write_text(json.dumps({"pid": os.getppid(), "version": "0.5.0", "percent": 40}))
        self.addCleanup((updates / "download.lock").unlink)
        self.assertEqual(update_install.start_download("0.4.0"),
                         {"state": "downloading", "version": "0.5.0", "percent": 40, "error": None})
        self.assertEqual(update_install.progress("0.4.0")["percent"], 40)
        self.assertEqual(self.release.requests, [])
        self.assertEqual(update_install.cancel("0.4.0")["state"], "downloading")
        self.assertTrue((updates / "download.lock").exists())

    def test_stale_lock_of_dead_process_is_taken_over(self):
        self.publish()
        finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True)
        updates = update_install.updates_dir()
        (updates / "0.4.0").mkdir(parents=True)
        (updates / "0.4.0/blanketa-0.4.0-linux-amd64.tar.gz.part").write_bytes(b"half")
        (updates / "0.5.0").mkdir()
        (updates / "0.5.0/blanketa-0.5.0-linux-amd64.tar.gz.part").write_bytes(b"half")
        (self.apps / ".blanketa-update-0.4.0").mkdir()
        (updates / "download.lock").write_text(json.dumps({"pid": int(finished.stdout), "version": "0.4.0", "percent": 3}))
        self.assertEqual(self.download()["state"], "ready")
        self.assertFalse((updates / "0.4.0").exists())
        self.assertFalse((self.apps / ".blanketa-update-0.4.0").exists())
        self.assertFalse((updates / "download.lock").exists())
        self.assertEqual(list(updates.rglob("*.part")), [])

    def test_damaged_lock_is_taken_over(self):
        updates = update_install.updates_dir()
        for minor, content in enumerate(("nesmysl", '{"pid": 0}', '{"pid": -1}', '{"pid": true}', '{"pid": "1"}'), start=5):
            with self.subTest(content=content):
                self.publish(f"0.{minor}.0")
                updates.mkdir(parents=True, exist_ok=True)
                (updates / "download.lock").write_text(content)
                os.utime(updates / "download.lock", (0, 0))
                self.assertEqual(self.download()["state"], "ready")

    def test_unreadable_lock_is_respected_and_kept(self):
        self.publish()
        lock = update_install.updates_dir() / "download.lock"
        lock.mkdir(parents=True)
        self.addCleanup(lock.rmdir)
        self.assertEqual(update_install.start_download("0.4.0")["state"], "downloading")
        self.assertTrue(lock.is_dir())
        self.assertEqual(self.release.requests, [])

    def test_failed_download_does_not_hide_another_process_or_a_ready_update(self):
        self.publish()
        self.release.files[self.archive_url()] = b"other"
        self.assertEqual(self.download()["error"], "verify")
        updates = update_install.updates_dir()
        (updates / "download.lock").write_text(json.dumps({"pid": os.getppid(), "version": "0.5.0", "percent": 7}))
        self.assertEqual(update_install.progress("0.4.0")["state"], "downloading")
        (updates / "download.lock").unlink()
        self.assertEqual(update_install.progress("0.4.0")["state"], "failed")
        write_ready(updates, self.apps)
        self.assertEqual(update_install.progress("0.4.0")["state"], "ready")

    def test_lock_being_written_by_another_process_is_respected(self):
        self.publish()
        updates = update_install.updates_dir()
        updates.mkdir(parents=True)
        (updates / "download.lock").write_text("")
        self.addCleanup((updates / "download.lock").unlink)
        self.assertEqual(update_install.start_download("0.4.0")["state"], "downloading")
        self.assertEqual(self.release.requests, [])

    def test_cancel_removes_partial_files(self):
        self.publish()
        gate = self.hold(self.archive_url())
        update_install.start_download("0.4.0")
        self.assertTrue(gate["reached"].wait(10))
        self.assertTrue(list(update_install.updates_dir().rglob("*.part")))
        threading.Timer(0.1, gate["release"].set).start()
        self.assertEqual(update_install.cancel("0.4.0"), {"state": "idle", "version": None, "percent": None, "error": None})
        self.assertEqual(self.leftovers(), [])
        self.assertIsNone(update_install.ready("0.4.0"))

    def test_progress_counts_the_percent_of_the_file(self):
        self.publish()
        gate = self.hold(self.archive_url())
        with mock.patch.object(update_install, "BYTES_PER_READ", 16):
            update_install.start_download("0.4.0")
            self.assertTrue(gate["reached"].wait(10))
            percent = update_install.progress("0.4.0")["percent"]
            gate["release"].set()
            self.wait()
        self.assertGreater(percent, 0)
        self.assertLess(percent, 100)

    def test_progress_without_a_length_has_no_percent(self):
        self.publish()
        url = self.archive_url()
        self.release.lengths[url] = None
        gate = self.hold(url)
        update_install.start_download("0.4.0")
        self.assertTrue(gate["reached"].wait(10))
        self.assertIsNone(update_install.progress("0.4.0")["percent"])
        gate["release"].set()
        self.assertEqual(self.wait()["state"], "ready")

    def test_nothing_downloaded_is_idle(self):
        self.assertEqual(update_install.progress("0.4.0"), {"state": "idle", "version": None, "percent": None, "error": None})
        self.assertEqual(update_install.cancel("0.4.0")["state"], "idle")


class Route(Downloads):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.server.handle_error = lambda request, address: None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token = app.new_session({"name": "zadost", "pdf": b""})

    def post(self, body, token=None):
        port = self.server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        connection.request("POST", f"/update-check?s={token or self.token}", body=body, headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        return response.status, response.read().decode()

    def test_route_downloads_reports_and_cancels(self):
        self.publish()
        status, body = self.post(b'{"action": "download"}')
        self.assertEqual((status, json.loads(body)["version"]), (200, "0.5.0"))
        self.wait()
        self.assertEqual(json.loads(self.post(b'{"action": "progress"}')[1])["state"], "ready")
        with mock.patch.object(update_install, "cancel", return_value={"state": "idle"}) as cancelled:
            self.assertEqual(json.loads(self.post(b'{"action": "cancel"}')[1]), {"state": "idle"})
        cancelled.assert_called_once_with(app.APP_VERSION)

    def test_route_refuses_unknown_actions(self):
        for body in (b'{"action": "install"}', b'{"action": "download", "url": "https://example.com/x"}',
                     b'{"action": "download", "version": "9.9.9"}', b'{"action": ["download"]}', b"[]", b"x"):
            with self.subTest(body=body):
                self.assertEqual(self.post(body)[0], 400)
        self.assertEqual(self.release.requests, [])

    def test_route_unavailable_in_settings_session(self):
        self.publish()
        settings = app.new_session({"settings": True})
        for action in ("check", "download", "progress", "cancel"):
            with self.subTest(action=action):
                self.assertEqual(self.post(json.dumps({"action": action}).encode(), settings)[0], 404)
        self.assertEqual(self.release.requests, [])


class UnexpectedFailure(Downloads):
    def test_unexpected_failure_releases_the_lock(self):
        self.publish()
        with mock.patch.object(update_install, "_outcome", side_effect=RuntimeError), \
                mock.patch.object(threading, "excepthook"):
            self.download()
        self.assertFalse((update_install.updates_dir() / "download.lock").exists())


def finished_process_id():
    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True)
    return int(finished.stdout)


class PendingInstall(Downloads):
    @classmethod
    def setUpClass(cls):
        cls.dead_pid = finished_process_id()

    def setUp(self):
        super().setUp()
        self.updates = update_install.updates_dir()
        self.updates.mkdir(parents=True)
        for patcher in (
            mock.patch.object(update_install, "running_version", return_value="0.4.0"),
            mock.patch.object(update_install.subprocess, "Popen"),
        ):
            self.launched = patcher.start()
            self.addCleanup(patcher.stop)

    def ready_for(self, kind, sha256=None, staged=None):
        self.kind = kind
        app = str(self.target()["app"])
        if kind == "windows-setup":
            staged = staged or self.updates / "0.5.0/Blanketa-0.5.0-setup.exe"
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(b"setup")
        else:
            folder = ".Blanketa-update-0.5.0/Blanketa.app" if kind == "mac-app" else ".blanketa-update-0.5.0/Blanketa"
            staged = staged or self.apps / folder
            staged.mkdir(parents=True, exist_ok=True)
        record = {"version": "0.5.0", "kind": kind, "app": app, "staged": str(staged), "all_users": kind == "windows-setup"}
        if kind == "windows-setup":
            record["sha256"] = sha256 or hashlib.sha256(b"setup").hexdigest()
        (self.updates / "ready.json").write_text(json.dumps(record))
        return record

    def start(self, relaunch=False, files=()):
        return update_install.start_pending_install(4321, relaunch, list(files))

    def running_entry(self, pid):
        (self.updates / "running").mkdir(exist_ok=True)
        (self.updates / "running" / str(pid)).touch()

    def test_nothing_starts_without_ready(self):
        self.assertFalse(self.start())
        self.launched.assert_not_called()

    def test_nothing_starts_while_another_process_runs(self):
        self.ready_for("linux-folder")
        self.running_entry(os.getppid())
        self.assertTrue(update_install.others_running())
        self.assertFalse(self.start())
        self.launched.assert_not_called()
        (self.updates / "running" / str(os.getppid())).unlink()
        self.assertTrue(self.start())

    def test_dead_process_entries_are_ignored(self):
        self.ready_for("linux-folder")
        self.running_entry(self.dead_pid)
        self.running_entry(os.getpid())
        (self.updates / "running/nesmysl").touch()
        self.assertFalse(update_install.others_running())
        self.assertTrue(self.start())

    def test_running_entry_is_registered_and_removed(self):
        update_install.register_running()
        self.assertTrue((self.updates / "running" / str(os.getpid())).exists())
        update_install.unregister_running()
        self.assertFalse((self.updates / "running" / str(os.getpid())).exists())

    def test_staged_path_outside_is_refused(self):
        elsewhere = self.folder / "elsewhere"
        for kind, staged in (
            ("linux-folder", elsewhere / "Blanketa"),
            ("linux-folder", self.apps / ".blanketa-update-0.5.0/../../elsewhere/Blanketa"),
            ("linux-folder", self.apps),
            ("windows-setup", elsewhere / "Blanketa-0.5.0-setup.exe"),
            ("windows-setup", self.apps / "Blanketa-0.5.0-setup.exe"),
        ):
            with self.subTest(kind=kind, staged=staged):
                self.ready_for(kind, staged=staged)
                self.assertFalse(self.start())
        self.launched.assert_not_called()

    def test_other_app_or_damaged_digest_is_refused(self):
        record = self.ready_for("linux-folder")
        (self.updates / "ready.json").write_text(json.dumps({**record, "app": str(self.folder / "jina")}))
        self.assertFalse(self.start())
        for digest in ("0" * 63, "g" * 64, "0" * 64 + "\n", "; rm -rf /" + "0" * 54):
            with self.subTest(digest=digest):
                self.ready_for("windows-setup", sha256=digest)
                self.assertFalse(self.start())
        self.launched.assert_not_called()

    def test_script_arguments_per_kind(self):
        files = ["/doklady/žádost.zfo", "relativni.pdf", "/doklady/b.pdf"]
        for kind in ("linux-folder", "mac-app"):
            with self.subTest(kind=kind):
                self.launched.reset_mock()
                record = self.ready_for(kind)
                self.assertTrue(self.start(relaunch=True, files=files))
                script = self.updates / "swap.sh"
                self.assertEqual(script.read_bytes(), (certstore.ROOT / "packaging/update/swap.sh").read_bytes())
                command, options = self.launched.call_args.args[0], self.launched.call_args.kwargs
                self.assertEqual(command, ["/bin/sh", str(script), "4321", record["staged"], record["app"], str(self.updates),
                                           "0.5.0", "1", "/doklady/žádost.zfo", "/doklady/b.pdf"])
                self.assertEqual((options["cwd"], options["start_new_session"]), (self.updates, True))
        self.launched.reset_mock()
        self.ready_for("linux-folder")
        self.start()
        self.assertEqual(self.launched.call_args.args[0][-1], "0")

    def test_windows_script_arguments(self):
        with mock.patch.object(update_install.certstore, "IS_WINDOWS", True), \
                mock.patch.dict(os.environ, {"SystemRoot": "/windows"}):
            record = self.ready_for("windows-setup")
            self.assertTrue(self.start(relaunch=True, files=["/doklady/a.zfo"]))
            script = self.updates / "swap.ps1"
            self.assertEqual(script.read_bytes(), (certstore.ROOT / "packaging/update/swap.ps1").read_bytes())
            command, options = self.launched.call_args.args[0], self.launched.call_args.kwargs
            self.assertEqual(command, [
                str(Path("/windows/System32/WindowsPowerShell/v1.0/powershell.exe")), "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(script), "-WaitPid", "4321", "-Kind", "windows-setup",
                "-Staged", record["staged"], "-Target", record["app"], "-UpdatesDir", str(self.updates), "-Version", "0.5.0",
                "-Sha256", record["sha256"], "-AllUsers", "-Relaunch", "/doklady/a.zfo"])
            self.assertEqual(options["cwd"], self.updates)
            self.assertEqual(options["creationflags"], 0x00000008 | 0x08000000 | 0x00000200)
            self.launched.reset_mock()
            record = self.ready_for("windows-portable")
            self.assertTrue(self.start())
            self.assertEqual(self.launched.call_args.args[0][7:], [
                "-WaitPid", "4321", "-Kind", "windows-portable", "-Staged", record["staged"], "-Target", record["app"],
                "-UpdatesDir", str(self.updates), "-Version", "0.5.0"])

    def test_failed_start_is_reported(self):
        self.ready_for("linux-folder")
        self.launched.side_effect = OSError
        self.assertFalse(self.start())

    def test_helper_script_locations(self):
        self.assertEqual(update_install.helper_script("linux-folder"), certstore.ROOT / "packaging/update/swap.sh")
        self.assertEqual(update_install.helper_script("windows-setup"), certstore.ROOT / "packaging/update/swap.ps1")
        with mock.patch.object(update_install.sys, "frozen", True, create=True):
            with mock.patch.object(update_install.certstore, "IS_MAC", True), \
                    mock.patch.object(update_install.sys, "executable", "/Applications/Blanketa.app/Contents/Resources/zfo-server/zfo-server"):
                self.assertEqual(update_install.helper_script("mac-app"),
                                 Path("/Applications/Blanketa.app/Contents/Resources/update/swap.sh"))
            with mock.patch.object(update_install.certstore, "IS_MAC", False), \
                    mock.patch.object(update_install.certstore, "RESOURCES", Path("/bundle")):
                self.assertEqual(update_install.helper_script("windows-portable"), Path("/bundle/update/swap.ps1"))
                self.assertEqual(update_install.helper_script("linux-folder"), Path("/bundle/update/swap.sh"))


class InstallUpdateMode(unittest.TestCase):
    def test_install_update_mode_of_the_server(self):
        with mock.patch.object(update_install, "start_pending_install", return_value=True) as started, \
                mock.patch.object(app, "ThreadingHTTPServer") as server:
            app.main(["--install-update", "123", "--relaunch", "/doklady/a.zfo", "/doklady/b.pdf"])
            started.assert_called_once_with(123, True, ["/doklady/a.zfo", "/doklady/b.pdf"])
            started.reset_mock()
            app.main(["--install-update", "123"])
            started.assert_called_once_with(123, False, [])
            started.reset_mock()
            for args in (["--install-update"], ["--install-update", "x"], ["--install-update", "-5"], ["--install-update", "0"],
                         ["--install-update", "\u0661"]):
                with self.subTest(args=args):
                    app.main(args)
            started.assert_not_called()
        server.assert_not_called()
