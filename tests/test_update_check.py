import http.client
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from support import (
    REAL_REFRESH_IN_BACKGROUND,
    isds_sample,
    needs_node,
    page_data,
    run_ui_scripts,
    ui_source,
)

import app
import crash_report
import isds_message
import update_check
import update_install

NOW = datetime(2026, 11, 24, 10, 0)
RELEASE_DOWNLOADS = "https://github.com/arosis/blanketa/releases/download/v0.5.0/"
ASSET_NAMES = [
    "blanketa-0.5.0-linux-amd64.tar.gz",
    "Blanketa-0.5.0-macos.dmg",
    "Blanketa-0.5.0-macos.zip",
    "Blanketa-0.5.0-setup.exe",
    "Blanketa-0.5.0-windows-x64.zip",
    "blanketa_0.5.0_amd64.deb",
]
ASSETS = {name: RELEASE_DOWNLOADS + name for name in ASSET_NAMES}


def release_json(tag="v0.5.0", assets=None):
    assets = ASSETS if assets is None else assets
    return json.dumps({
        "tag_name": tag,
        "assets": [{"name": name, "browser_download_url": url} for name, url in assets.items()],
    }).encode()


class FakeOpener:
    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return io.BytesIO(self.answer)


class WithOwnSettingsFolder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.settings = self.folder / update_check.SETTINGS_FILE
        for patcher in (
            mock.patch.object(update_check.certstore, "config_dir", return_value=self.folder),
            mock.patch.object(update_check, "PLATFORM", "mac"),
            mock.patch.object(update_check, "portable_windows", return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(setattr, update_check, "_background_running", False)
        self.new_window()
        self.addCleanup(self.new_window)
        self.real_refresh = REAL_REFRESH_IN_BACKGROUND
        patcher = mock.patch.object(update_check, "refresh_in_background")
        patcher.start()
        self.addCleanup(patcher.stop)

    def new_window(self):
        update_check._window_considered = False

    def store(self, **values):
        self.settings.write_text(json.dumps(values), encoding="utf-8")

    def fresh_state(self, version="0.5.0", **more):
        self.store(latest={"version": version, "assets": ASSETS}, checked_at=NOW.isoformat(), **more)

    def answering(self, answer):
        opener = FakeOpener(answer)
        patcher = mock.patch.object(update_check.public_services, "opener", return_value=opener)
        patcher.start()
        self.addCleanup(patcher.stop)
        return opener


class VersionTuple(unittest.TestCase):
    def test_only_plain_version_tags_count(self):
        self.assertEqual(update_check.version_tuple("0.4.0"), (0, 4, 0))
        self.assertEqual(update_check.version_tuple("v0.4.0"), (0, 4, 0))
        for text in ("v0.5.0-rc1", "latest", "0.5", "", None, 5, "v\u0661.\u0662.\u0663"):
            with self.subTest(text=text):
                self.assertIsNone(update_check.version_tuple(text))


class Offer(WithOwnSettingsFolder):
    def test_newer_release_is_offered_once_then_postponed_a_week(self):
        self.fresh_state()
        offer = update_check.offer_for_page("0.4.0", NOW)
        self.assertEqual(offer["version"], "0.5.0")
        self.assertEqual(offer["current"], "0.4.0")
        self.new_window()
        self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))
        self.new_window()
        self.assertIsNone(update_check.offer_for_page("0.4.0", datetime(2026, 11, 30, 10, 0)))
        self.new_window()
        self.assertIsNotNone(update_check.offer_for_page("0.4.0", datetime(2026, 12, 1, 10, 0)))

    def test_only_the_first_render_of_a_window_may_offer(self):
        self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))
        self.fresh_state()
        self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))
        self.new_window()
        self.assertEqual(update_check.offer_for_page("0.4.0", NOW)["version"], "0.5.0")

    def test_the_flag_does_not_leak_between_tests(self):
        self.assertFalse(update_check._window_considered)

    def test_newer_version_than_postponed_is_offered_at_once(self):
        self.fresh_state("0.5.1", postponed={"version": "0.5.0", "until": datetime(2026, 12, 1, 10, 0).isoformat()})
        self.assertEqual(update_check.offer_for_page("0.4.0", NOW)["version"], "0.5.1")

    def test_same_or_older_release_is_not_offered(self):
        for version in ("0.4.0", "0.3.0"):
            with self.subTest(version=version):
                self.fresh_state(version)
                self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))

    def test_postponement_from_the_future_does_not_hold(self):
        self.fresh_state(postponed={"version": "0.5.0", "until": datetime(2027, 6, 1).isoformat()})
        self.assertIsNotNone(update_check.offer_for_page("0.4.0", NOW))

    def test_turned_off_offers_nothing_and_asks_nothing(self):
        opener = self.answering(release_json())
        update_check.set_enabled(False)
        self.assertFalse(update_check.enabled())
        self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))
        self.assertEqual(opener.requests, [])

    def test_turned_on_again(self):
        update_check.set_enabled(False)
        update_check.set_enabled(True)
        self.assertTrue(update_check.enabled())

    def test_stale_result_is_refreshed_in_background_without_waiting(self):
        opener = self.answering(release_json())
        with mock.patch.object(update_check, "refresh_in_background") as refresh:
            self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))
        refresh.assert_called_once_with()
        self.assertEqual(opener.requests, [])

    def test_fresh_result_is_not_refreshed(self):
        self.fresh_state()
        with mock.patch.object(update_check, "refresh_in_background") as refresh:
            update_check.offer_for_page("0.4.0", NOW)
        refresh.assert_not_called()

    def test_result_older_than_a_day_is_refreshed(self):
        self.fresh_state()
        with mock.patch.object(update_check, "refresh_in_background") as refresh:
            update_check.offer_for_page("0.4.0", datetime(2026, 11, 25, 10, 0))
        refresh.assert_called_once_with()

    def test_damaged_settings_count_as_none(self):
        for content in ("[]", '{"latest": 5}', '{"latest": {"version": "x"}, "checked_at": 3, "postponed": 1}', "nesmysl"):
            with self.subTest(content=content):
                self.settings.write_text(content, encoding="utf-8")
                with mock.patch.object(update_check, "refresh_in_background"):
                    self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))
                self.assertTrue(update_check.enabled())

    def test_unwritable_settings_never_break_the_offer(self):
        self.fresh_state()
        with mock.patch.object(update_check, "_store", side_effect=OSError):
            self.assertEqual(update_check.offer_for_page("0.4.0", NOW)["version"], "0.5.0")
        self.answering(release_json())
        with mock.patch.object(update_check, "_store", side_effect=OSError):
            self.assertEqual(update_check.check_now("0.4.0", NOW)["status"], "newer")

    def test_settings_folder_that_is_a_file(self):
        blocker = self.folder / "file"
        blocker.write_text("x")
        self.answering(release_json())
        with mock.patch.object(update_check.certstore, "config_dir", return_value=blocker):
            self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))
            self.assertEqual(update_check.check_now("0.4.0", NOW)["status"], "newer")


class Request(WithOwnSettingsFolder):
    def test_request_sends_no_version(self):
        opener = self.answering(release_json())
        update_check.fetch_latest()
        (request,) = opener.requests
        self.assertEqual(request.full_url, "https://api.github.com/repos/arosis/blanketa/releases/latest")
        self.assertEqual(request.headers, {"User-agent": "Blanketa"})

    def test_unusable_answers_count_as_unknown(self):
        answers = (
            urllib.error.HTTPError("https://api.github.com", 403, "rate limit", {}, None),
            urllib.error.URLError("offline"),
            b"<html>",
            b"{}",
            b"[]",
            b'{"tag_name": 5}',
        )
        for answer in answers:
            with self.subTest(answer=repr(answer)):
                self.answering(answer)
                self.assertIsNone(update_check.fetch_latest())
                self.assertEqual(update_check.check_now("0.4.0", NOW), {"status": "unknown"})

    def test_only_plain_version_tags_count(self):
        for tag in ("v0.5.0-rc1", "0.5.0", "latest", "v\u0661.\u0662.\u0663"):
            with self.subTest(tag=tag):
                self.answering(release_json(tag))
                self.assertIsNone(update_check.fetch_latest())
        self.answering(release_json("v0.5.0"))
        self.assertEqual(update_check.fetch_latest()["version"], "0.5.0")


class DownloadUrl(WithOwnSettingsFolder):
    def offer(self, assets=None):
        self.new_window()
        self.store(latest={"version": "0.5.0", "assets": ASSETS if assets is None else assets}, checked_at=NOW.isoformat())
        return update_check.offer_for_page("0.4.0", NOW)

    def test_download_url_matches_platform(self):
        offer = self.offer()
        self.assertEqual(offer["download_url"], RELEASE_DOWNLOADS + "Blanketa-0.5.0-macos.dmg")
        self.assertEqual(offer["news_url"], "https://blanketa.cz/novinky/#0.5.0")
        self.assertFalse(offer["linux"])
        with mock.patch.object(update_check, "PLATFORM", "windows"):
            self.assertEqual(self.offer()["download_url"], RELEASE_DOWNLOADS + "Blanketa-0.5.0-setup.exe")
            with mock.patch.object(update_check, "portable_windows", return_value=True):
                self.assertEqual(self.offer()["download_url"], RELEASE_DOWNLOADS + "Blanketa-0.5.0-windows-x64.zip")
        with mock.patch.object(update_check, "PLATFORM", "linux"):
            offer = self.offer()
            self.assertEqual(offer["download_url"], "https://github.com/arosis/blanketa/releases/tag/v0.5.0")
            self.assertTrue(offer["linux"])

    def test_foreign_asset_url_falls_back_to_release_page(self):
        name = "Blanketa-0.5.0-macos.dmg"
        for url in (
            "https://example.com/arosis/blanketa/releases/download/v0.5.0/" + name,
            "http://github.com/arosis/blanketa/releases/download/v0.5.0/" + name,
            "https://github.com/arosis/blanketa/releases/download/v0.4.0/" + name,
            RELEASE_DOWNLOADS + "../../../../evil/x/releases/download/v1/" + name,
            RELEASE_DOWNLOADS + "..%2f..%2fevil/" + name,
            RELEASE_DOWNLOADS + "%2e%2e/" + name,
            RELEASE_DOWNLOADS + "..\\" + name,
            RELEASE_DOWNLOADS + name + "?x=1",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    self.offer({name: url})["download_url"],
                    "https://github.com/arosis/blanketa/releases/tag/v0.5.0",
                )

    def test_missing_asset_falls_back_to_release_page(self):
        self.assertEqual(self.offer({})["download_url"], "https://github.com/arosis/blanketa/releases/tag/v0.5.0")


MANIFEST = "blanketa-0.5.0-update.json"
INSTALLABLE_ASSETS = {**ASSETS, MANIFEST: RELEASE_DOWNLOADS + MANIFEST, MANIFEST + ".sig": RELEASE_DOWNLOADS + MANIFEST + ".sig"}
class Installable(WithOwnSettingsFolder):
    def setUp(self):
        super().setUp()
        self.applications = self.folder / "Applications"
        self.applications.mkdir()
        self.mac_target = {"kind": "mac-app", "app": self.applications / "Blanketa.app", "all_users": False}

    def offer(self, assets, target="mac", reason=None):
        target = self.mac_target if target == "mac" else target
        self.new_window()
        self.store(latest={"version": "0.5.0", "assets": assets}, checked_at=NOW.isoformat())
        with mock.patch.object(update_install, "target", return_value=target), \
                mock.patch.object(update_install, "not_replaceable_reason", return_value=reason):
            return update_check.offer_for_page("0.4.0", NOW)

    def test_installable_offer(self):
        offer = self.offer(INSTALLABLE_ASSETS)
        self.assertTrue(offer["install"])
        self.assertIsNone(offer["reason"])
        self.assertIsNone(offer["failed"])
        self.assertEqual(offer["download_url"], RELEASE_DOWNLOADS + "Blanketa-0.5.0-macos.dmg")

    def test_release_without_manifest_offers_download(self):
        for assets in (ASSETS, {**ASSETS, MANIFEST: INSTALLABLE_ASSETS[MANIFEST]}, {**ASSETS, MANIFEST + ".sig": INSTALLABLE_ASSETS[MANIFEST + ".sig"]}):
            with self.subTest(assets=sorted(assets)):
                offer = self.offer(assets)
                self.assertFalse(offer["install"])
                self.assertEqual(offer["download_url"], RELEASE_DOWNLOADS + "Blanketa-0.5.0-macos.dmg")

    def test_manifest_url_with_other_path_offers_download(self):
        for name in (MANIFEST, MANIFEST + ".sig"):
            for url in (
                "https://example.com/" + name,
                "https://github.com/arosis/blanketa/releases/download/v0.4.0/" + name,
                RELEASE_DOWNLOADS + name + "?x=1",
                RELEASE_DOWNLOADS + "../" + name,
            ):
                with self.subTest(name=name, url=url):
                    self.assertFalse(self.offer({**INSTALLABLE_ASSETS, name: url})["install"])

    def test_missing_or_foreign_installable_file_offers_download(self):
        archive = "Blanketa-0.5.0-macos.zip"
        without = {name: url for name, url in INSTALLABLE_ASSETS.items() if name != archive}
        self.assertFalse(self.offer(without)["install"])
        self.assertFalse(self.offer({**INSTALLABLE_ASSETS, archive: "https://example.com/" + archive})["install"])

    def test_each_kind_needs_its_own_file(self):
        kinds = {
            "windows-setup": "Blanketa-0.5.0-setup.exe",
            "windows-portable": "Blanketa-0.5.0-windows-x64.zip",
            "linux-folder": "blanketa-0.5.0-linux-amd64.tar.gz",
        }
        for kind, name in kinds.items():
            target = {"kind": kind, "app": Path("/x"), "all_users": False}
            with self.subTest(kind=kind):
                self.assertTrue(self.offer(INSTALLABLE_ASSETS, target)["install"])
                without = {n: u for n, u in INSTALLABLE_ASSETS.items() if n != name}
                self.assertFalse(self.offer(without, target)["install"])

    def test_installable_linux_offer_has_no_manual_install_line(self):
        folder = {"kind": "linux-folder", "app": Path("/home/u/.local/share/blanketa"), "all_users": False}
        with mock.patch.object(update_check, "PLATFORM", "linux"):
            installable = self.offer(INSTALLABLE_ASSETS, folder)
            manual = self.offer(INSTALLABLE_ASSETS, None)
        self.assertEqual((installable["install"], installable["linux"]), (True, False))
        self.assertEqual((manual["install"], manual["linux"]), (False, True))

    def test_not_replaceable_copy_carries_the_reason(self):
        offer = self.offer(INSTALLABLE_ASSETS, None, "move-to-applications")
        self.assertFalse(offer["install"])
        self.assertEqual(offer["reason"], "move-to-applications")

    def test_failed_install_is_reported_once(self):
        failed = update_install.updates_dir() / "failed.json"
        failed.parent.mkdir(parents=True, exist_ok=True)
        failed.write_text(json.dumps({"version": "0.5.0"}))
        self.assertEqual(self.offer(INSTALLABLE_ASSETS)["failed"], {"version": "0.5.0"})
        self.assertFalse(failed.exists())
        self.assertIsNone(self.offer(INSTALLABLE_ASSETS)["failed"])

    def write_failed(self):
        failed = update_install.updates_dir() / "failed.json"
        failed.parent.mkdir(parents=True, exist_ok=True)
        failed.write_text(json.dumps({"version": "0.5.0"}))
        return failed

    def test_reported_failure_removes_its_staging_and_download_only(self):
        updates = update_install.updates_dir()
        removed = [updates / "0.5.0", self.applications / ".Blanketa-update-0.5.0"]
        kept = [updates / "0.6.0", self.applications / ".Blanketa-update-0.6.0", self.applications / "Blanketa.app",
                self.applications / "Other.app", updates / "running"]
        for folder in removed + kept:
            (folder / "inside").mkdir(parents=True)
        self.write_failed()
        self.assertEqual(self.offer(INSTALLABLE_ASSETS)["failed"], {"version": "0.5.0"})
        self.assertEqual([folder for folder in removed if folder.exists()], [])
        self.assertEqual([folder for folder in kept if not (folder / "inside").is_dir()], [])

    def test_failed_install_is_reported_despite_postponement(self):
        self.offer(INSTALLABLE_ASSETS)
        failed = self.write_failed()
        self.new_window()
        offer = update_check.offer_for_page("0.4.0", NOW)
        self.assertEqual(offer["failed"], {"version": "0.5.0"})
        self.assertFalse(failed.exists())
        self.new_window()
        self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))

    def test_failed_install_is_reported_without_a_newer_release(self):
        self.store(checked_at=NOW.isoformat())
        self.write_failed()
        offer = update_check.offer_for_page("0.4.0", NOW)
        self.assertEqual(offer["failed"], {"version": "0.5.0"})
        self.assertEqual(offer["download_url"], "https://github.com/arosis/blanketa/releases/tag/v0.5.0")
        self.assertFalse(offer["install"])

    def test_failed_install_is_reported_when_checks_are_off(self):
        update_check.set_enabled(False)
        failed = self.write_failed()
        self.new_window()
        self.assertEqual(update_check.offer_for_page("0.4.0", NOW)["failed"], {"version": "0.5.0"})
        self.assertFalse(failed.exists())

    def test_failed_note_waits_for_the_first_render_of_a_window(self):
        failed = self.write_failed()
        update_check._window_considered = True
        self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))
        self.assertTrue(failed.exists())

    def test_malformed_failure_note_is_ignored_and_deleted(self):
        failed = update_install.updates_dir() / "failed.json"
        failed.parent.mkdir(parents=True, exist_ok=True)
        for content in ("nesmysl", "[]", '{"version": 5}', '{"version": "x"}', "{}"):
            with self.subTest(content=content):
                failed.write_text(content)
                self.assertIsNone(self.offer(INSTALLABLE_ASSETS)["failed"])
                self.assertFalse(failed.exists())

    def test_source_run_cannot_install(self):
        self.new_window()
        self.store(latest={"version": "0.5.0", "assets": INSTALLABLE_ASSETS}, checked_at=NOW.isoformat())
        self.assertFalse(update_check.offer_for_page("0.4.0", NOW)["install"])


class ManualCheck(WithOwnSettingsFolder):
    def test_manual_check_works_when_turned_off(self):
        update_check.set_enabled(False)
        self.answering(release_json())
        result = update_check.check_now("0.4.0", NOW)
        self.assertEqual(result["status"], "newer")
        self.assertEqual(result["offer"]["version"], "0.5.0")
        self.assertFalse(update_check.enabled())

    def test_manual_check_stores_result_and_postpones_the_offer(self):
        self.answering(release_json())
        update_check.check_now("0.4.0", NOW)
        self.assertIsNone(update_check.offer_for_page("0.4.0", NOW))

    def test_current_release_is_reported_as_current(self):
        self.answering(release_json("v0.4.0"))
        self.assertEqual(update_check.check_now("0.4.0", NOW), {"status": "current"})


class BackgroundRefresh(WithOwnSettingsFolder):
    def test_refresh_stores_result_and_check_time(self):
        self.answering(release_json())
        update_check._refresh()
        stored = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(stored["latest"]["version"], "0.5.0")
        self.assertIn("checked_at", stored)

    def test_failed_refresh_still_remembers_the_attempt(self):
        self.answering(urllib.error.URLError("offline"))
        update_check._refresh()
        stored = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertIn("checked_at", stored)
        self.assertNotIn("latest", stored)

    def test_only_one_refresh_runs_at_a_time(self):
        started = []
        with mock.patch.object(update_check.threading, "Thread") as thread:
            thread.side_effect = lambda **kwargs: started.append(kwargs) or mock.Mock()
            self.real_refresh()
            self.real_refresh()
        self.assertEqual(len(started), 1)


class PortableWindows(unittest.TestCase):
    def test_not_frozen_is_not_portable(self):
        with mock.patch.object(update_check.certstore, "IS_WINDOWS", True):
            self.assertFalse(update_check.portable_windows())

    def test_frozen_windows_build_without_uninstaller_is_portable(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(update_check.certstore, "IS_WINDOWS", True), \
                mock.patch.object(update_check.sys, "frozen", True, create=True), \
                mock.patch.object(update_check.sys, "executable", str(Path(folder) / "Blanketa.exe")):
            self.assertTrue(update_check.portable_windows())
            (Path(folder) / "unins000.exe").write_text("")
            self.assertFalse(update_check.portable_windows())

    def test_other_systems_are_never_portable_windows(self):
        with mock.patch.object(update_check.certstore, "IS_WINDOWS", False), \
                mock.patch.object(update_check.sys, "frozen", True, create=True):
            self.assertFalse(update_check.portable_windows())


OFFER = {
    "version": "0.5.0",
    "current": app.APP_VERSION,
    "download_url": RELEASE_DOWNLOADS + "Blanketa-0.5.0-macos.dmg",
    "news_url": "https://blanketa.cz/novinky/#0.5.0",
    "linux": False,
}


class OnThePages(WithOwnSettingsFolder):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(crash_report, "settings_path", return_value=self.folder / crash_report.SETTINGS_FILE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_page_carries_a_stored_offer(self):
        with mock.patch.object(update_check, "offer_for_page", return_value=OFFER) as offered:
            data = page_data(app.document_page(app.SHELL, "formulář ZFO", {"session": "x"}))
        offered.assert_called_once_with(app.APP_VERSION)
        self.assertEqual(data["update_offer"]["version"], "0.5.0")
        self.assertNotIn("update_checks", data)

    def test_page_without_an_offer_says_so(self):
        with mock.patch.object(update_check, "offer_for_page", return_value=None):
            data = page_data(app.render_empty_window())
        self.assertIsNone(data["update_offer"])

    def test_crash_offer_wins_and_records_nothing(self):
        with mock.patch.object(crash_report, "take_pending", return_value="Chyba serveru"), \
                mock.patch.object(update_check, "offer_for_page") as offered:
            data = page_data(app.render_empty_window())
        self.assertIsNone(data["update_offer"])
        offered.assert_not_called()

    def test_page_renders_when_settings_are_unwritable(self):
        with mock.patch.object(update_check.certstore, "config_dir", return_value=self.folder / "missing" / "deeper"):
            data = page_data(app.render_empty_window())
        self.assertIsNone(data["update_offer"])
        self.assertNotIn("update_checks", data)


class InTheLocalServer(WithOwnSettingsFolder):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.server.handle_error = lambda request, address: None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token = app.new_session({"name": "zadost", "pdf": b""})

    def post(self, path, body=b"{}", token=None):
        port = self.server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        connection.request("POST", f"{path}?s={token or self.token}", body=body, headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        return response.status, response.read().decode()

    def test_manual_check_answers_with_the_result(self):
        answer = {"status": "newer", "offer": OFFER}
        with mock.patch.object(update_check, "check_now", return_value=answer) as checked:
            status, body = self.post("/update-check", b'{"action": "check"}')
        checked.assert_called_once_with(app.APP_VERSION)
        self.assertEqual((status, json.loads(body)), (200, answer))

    def test_switch_is_changed_only_in_settings(self):
        for body in (b'{"enabled": false}', b'{"enabled": true}', b'{"action": "state"}'):
            with self.subTest(body=body):
                self.assertEqual(self.post("/update-check", body)[0], 400)
        self.assertTrue(update_check.enabled())

    def test_malformed_body_is_refused(self):
        for body in (b"x", b"[]", b"{}", b'{"action": "rm"}', b'{"enabled": "no"}', b'{"action": "check", "enabled": true}'):
            with self.subTest(body=body):
                self.assertEqual(self.post("/update-check", body)[0], 400)
        self.assertFalse(self.settings.exists())

    def test_message_container_and_empty_windows_may_use_it(self):
        message = app.new_session({"name": "zprava", "message": isds_message.read_message(isds_sample("prijata"))})
        container = app.new_session({"name": "kontejner", "entries": []})
        empty = app.new_session({"empty": True})
        for token in (message, container, empty):
            with self.subTest(token=token):
                with mock.patch.object(update_check, "check_now", return_value={"status": "current"}):
                    self.assertEqual(self.post("/update-check", b'{"action": "check"}', token)[0], 200)
                self.assertEqual(self.post("/save", b"{}", token)[0], 404)


@needs_node
class UpdateDialog(unittest.TestCase):
    def offer_text(self, **fields):
        offer = {"version": "0.5.0", "current": "0.4.0", "linux": False, "download_url": RELEASE_DOWNLOADS, "news_url": "https://blanketa.cz/novinky/#0.5.0"}
        offer.update(fields)
        return json.loads(run_ui_scripts(["bridge.js"], f"console.log(JSON.stringify(zfoUpdateOffer.text({json.dumps(offer)})));"))

    def test_offer_text_names_both_versions(self):
        text = self.offer_text()
        self.assertEqual(text["title"], "Je k dispozici nová verze Blankety")
        self.assertEqual(text["lines"], ["Blanketa 0.5.0 (máte 0.4.0)"])

    def test_linux_offer_explains_installation(self):
        lines = self.offer_text(linux=True)["lines"]
        self.assertEqual(lines[0], "Blanketa 0.5.0 (máte 0.4.0)")
        self.assertIn("sudo apt install ./blanketa_0.5.0_amd64.deb", lines[1])

    def test_installable_linux_folder_shows_no_apt_line(self):
        folder = {"kind": "linux-folder", "app": Path("/home/u/.local/share/blanketa"), "all_users": False}
        latest = {"version": "0.5.0", "assets": INSTALLABLE_ASSETS}
        for target, has_apt_line in ((folder, False), (None, True)):
            with self.subTest(target=target), mock.patch.object(update_check, "PLATFORM", "linux"), \
                    mock.patch.object(update_install, "target", return_value=target), \
                    mock.patch.object(update_check, "_failed_install", return_value=None):
                offer = update_check._offer(latest, "0.4.0")
            screen = json.loads(run_ui_scripts(["bridge.js"], f"console.log(JSON.stringify(zfoUpdateOffer.screen({json.dumps(offer)})));"))
            self.assertEqual(any("sudo apt install" in line for line in screen["lines"]), has_apt_line)

    def test_bridge_opens_only_the_offered_urls(self):
        source = ui_source("bridge.js")
        block = source[source.index("window.zfoUpdateOffer"):]
        self.assertIn("window.zfoOpenUrl(offer.download_url)", block)
        self.assertIn("window.zfoOpenUrl(offer.news_url)", block)
        self.assertNotIn("window.open(", block)

    def test_offer_dialog_waits_for_page_load_like_the_crash_offer(self):
        source = ui_source("bridge.js")
        block = source[source.index("window.zfoUpdateOffer"):]
        self.assertIn("pageData().update_offer", block)
        self.assertIn("document.readyState === 'loading'", block)
        self.assertIn("document.addEventListener('DOMContentLoaded', offerOnOpen)", block)


FAKE_DIALOGS = """
    const timers = [], dialogs = [], posted = [], sent = [], opened = [];
    window.zfoOpenUrl = url => opened.push(url);
    window.zfoNative = {postMessage: message => sent.push(message)};
    globalThis.setTimeout = callback => timers.push(callback) - 1;
    globalThis.clearTimeout = () => {};
    document.body.append = () => {};
    document.createElement = tag => {
        const buttons = {};
        const created = {style: {}, text: [], texts: {}, closed: false, _html: '',
            set innerHTML(html) { this._html = html; this.text = []; this.texts = {}; }, get innerHTML() { return this._html; }, append() {},
                        addEventListener(name, handler) { if (name === 'close') this.onclose = handler; },
            close() { this.closed = true; this.onclose?.(); }, remove() {}, showModal() { this.shown = true; },
            querySelector(selector) {
                const id = /data-id="(.*)"/.exec(selector)?.[1];
                if (id) return this.innerHTML.includes(`data-id="${id}"`) ? (buttons[id] ??= {addEventListener: (_, handler) => { buttons[id].click = handler; }}) : null;
                return this.texts[selector] ??= {addEventListener() {}, append: child => this.text.push(child.textContent)};
            },
            get buttons() { return [...this.innerHTML.matchAll(/data-id="(\\w+)">([^<]*)</g)].map(match => match[2]); },
            press(id) { return this.querySelector(`[data-id="${id}"]`) && buttons[id].click(); }};
        if (tag === 'dialog') dialogs.push(created);
        return created;
    };
    const answers = [];
    fetch = async (url, init) => { posted.push(JSON.parse(init.body)); return {ok: true, json: async () => answers.shift()}; };
    const settle = async () => { for (let turn = 0; turn < 20; turn++) await null; };
    const lastDialog = () => dialogs[dialogs.length - 1];
"""


@needs_node
class UpdateScreens(unittest.TestCase):
    OFFER = {"version": "0.5.0", "current": "0.4.0", "linux": False, "download_url": "D", "news_url": "N",
             "install": True, "reason": None, "failed": None}

    def screen(self, state=None, **fields):
        script = f"console.log(JSON.stringify(zfoUpdateOffer.screen({json.dumps({**self.OFFER, **fields})}, {json.dumps(state)} ?? undefined)));"
        return json.loads(run_ui_scripts(["bridge.js"], script))

    def labels(self, view):
        return [item["label"] for item in view["buttons"]]

    def test_installable_offer_says_update(self):
        view = self.screen()
        self.assertEqual(self.labels(view), ["Co je nového", "Připomenout za týden", "Aktualizovat"])
        self.assertEqual(view["lines"], ["Blanketa 0.5.0 (máte 0.4.0)"])

    def test_offer_that_cannot_install_says_download(self):
        self.assertEqual(self.labels(self.screen(install=False))[-1], "Stáhnout…")

    def test_mac_that_cannot_be_replaced_explains_the_move(self):
        view = self.screen(install=False, reason="move-to-applications")
        self.assertEqual(view["lines"][-1], "Blanketu nejde aktualizovat, dokud běží z obrazu disku nebo ze Stažených souborů. "
                         "Přesuňte ji do složky Aplikace a otevřete ji znovu.")
        self.assertNotIn("Přesuňte", " ".join(self.screen()["lines"]))

    def test_mac_without_write_permission_asks_for_a_manual_download(self):
        view = self.screen(install=False, reason="not-writable")
        self.assertEqual(view["lines"][-1], "Blanketu teď nejde aktualizovat, protože do její složky nemáte právo zapisovat. "
                         "Stáhněte novou verzi ručně.")
        self.assertEqual(self.labels(view)[-1], "Stáhnout…")
        self.assertEqual(self.screen(reason="not-writable")["lines"], ["Blanketa 0.5.0 (máte 0.4.0)"])

    def test_progress_shows_percent_or_none(self):
        view = self.screen({"name": "progress", "percent": 42})
        self.assertEqual(view["lines"], ["Stahuji Blanketu 0.5.0… 42\u00a0%"])
        self.assertEqual(self.labels(view), ["Zrušit"])
        self.assertEqual(self.screen({"name": "progress", "percent": None})["lines"], ["Stahuji Blanketu 0.5.0…"])
        self.assertEqual(self.screen({"name": "progress", "percent": 0})["lines"], ["Stahuji Blanketu 0.5.0… 0\u00a0%"])

    def test_ready_dialog(self):
        view = self.screen({"name": "ready"})
        self.assertEqual(view["lines"], ["Blanketa 0.5.0 je připravená. Nainstaluje se, až Blanketu zavřete."])
        self.assertEqual(self.labels(view), ["Později", "Restartovat teď"])

    def test_failures_offer_the_download(self):
        verify = self.screen({"name": "verify"})
        self.assertEqual(verify["lines"], ["Aktualizaci se nepodařilo ověřit, nic se nezměnilo."])
        download = self.screen({"name": "download"})
        self.assertEqual(download["lines"], ["Aktualizaci se nepodařilo stáhnout. Zkuste to později."])
        for view in (verify, download):
            self.assertEqual(self.labels(view), ["Zavřít", "Stáhnout…"])

    def test_failed_install_on_its_own_is_a_report(self):
        view = self.screen(install=False, failed={"version": "0.5.0"})
        self.assertEqual(view["lines"], ["Aktualizaci na Blanketu 0.5.0 se nepodařilo nainstalovat, zůstala verze 0.4.0."])
        self.assertEqual(self.labels(view), ["Zavřít", "Stáhnout…"])

    def test_failed_install_beside_a_newer_release_comes_first(self):
        view = self.screen(version="0.6.0", failed={"version": "0.5.0"})
        self.assertEqual(view["lines"][0], "Aktualizaci na Blanketu 0.5.0 se nepodařilo nainstalovat, zůstala verze 0.4.0.")
        self.assertEqual(view["lines"][1], "Blanketa 0.6.0 (máte 0.4.0)")
        self.assertEqual(self.labels(view)[-1], "Aktualizovat")


@needs_node
class UpdateFlow(unittest.TestCase):
    def run_flow(self, body):
        return json.loads(run_ui_scripts(["bridge.js"], FAKE_DIALOGS + "(async () => {" + body + "})();"))

    OFFER = "{version: '0.5.0', current: '0.4.0', linux: false, download_url: 'D', news_url: 'N', install: true, reason: null, failed: null}"

    def test_update_polls_until_ready_and_sends_nothing_but_actions(self):
        result = self.run_flow(f"""
            answers.push({{state: 'downloading', percent: null}}, {{state: 'downloading', percent: 40}}, {{state: 'ready', percent: 100}});
            zfoUpdateOffer({self.OFFER});
            lastDialog().press('install');
            await settle();
            const first = lastDialog().text.join('|');
            timers.shift()(); await settle();
            const second = lastDialog().text.join('|');
            timers.shift()(); await settle();
            console.log(JSON.stringify({{posted, first, second, ready: lastDialog().text.join('|'), buttons: lastDialog().buttons,
                waits: timers.length}}));
        """)
        self.assertEqual(result["posted"], [{"action": "download"}, {"action": "progress"}, {"action": "progress"}])
        self.assertEqual(result["first"], "Stahuji Blanketu 0.5.0…")
        self.assertEqual(result["second"], "Stahuji Blanketu 0.5.0… 40\u00a0%")
        self.assertIn("je připravená", result["ready"])
        self.assertEqual(result["buttons"], ["Později", "Restartovat teď"])
        self.assertEqual(result["waits"], 0)

    def test_restart_sends_only_the_action(self):
        result = self.run_flow("""
            zfoUpdateOffer(%s, {name: 'ready'});
            lastDialog().press('restart');
            console.log(JSON.stringify({sent, closed: lastDialog().closed}));
        """ % self.OFFER)
        self.assertEqual(result["sent"], [{"action": "installUpdate", "restart": True}])
        self.assertTrue(result["closed"])

    def test_later_just_closes_the_ready_dialog(self):
        result = self.run_flow("""
            zfoUpdateOffer(%s, {name: 'ready'});
            lastDialog().press('later');
            console.log(JSON.stringify({sent, posted, closed: lastDialog().closed}));
        """ % self.OFFER)
        self.assertEqual((result["sent"], result["posted"], result["closed"]), ([], [], True))

    def test_cancel_stops_polling_and_closes(self):
        result = self.run_flow(f"""
            answers.push({{state: 'downloading', percent: 5}}, {{state: 'idle'}});
            zfoUpdateOffer({self.OFFER});
            lastDialog().press('install'); await settle();
            lastDialog().press('cancel'); await settle();
            timers.shift()(); await settle();
            console.log(JSON.stringify({{posted, closed: lastDialog().closed}}));
        """)
        self.assertEqual(result["posted"], [{"action": "download"}, {"action": "cancel"}])
        self.assertTrue(result["closed"])

    def test_verify_failure_while_downloading(self):
        result = self.run_flow(f"""
            answers.push({{state: 'downloading', percent: 5}}, {{state: 'failed', error: 'verify'}});
            zfoUpdateOffer({self.OFFER});
            lastDialog().press('install'); await settle();
            timers.shift()(); await settle();
            lastDialog().press('download');
            console.log(JSON.stringify({{text: lastDialog().text, opened}}));
        """)
        self.assertEqual(result["text"], ["Aktualizaci se nepodařilo ověřit, nic se nezměnilo."])
        self.assertEqual(result["opened"], ["D"])

    def test_download_failure_and_unreachable_server(self):
        result = self.run_flow(f"""
            answers.push({{state: 'failed', error: 'download'}});
            zfoUpdateOffer({self.OFFER});
            lastDialog().press('install'); await settle();
            const first = lastDialog().text;
            fetch = async () => {{ throw new Error('x'); }};
            zfoUpdateOffer({self.OFFER});
            lastDialog().press('install'); await settle();
            console.log(JSON.stringify({{first, second: lastDialog().text}}));
        """)
        expected = ["Aktualizaci se nepodařilo stáhnout. Zkuste to později."]
        self.assertEqual((result["first"], result["second"]), (expected, expected))

    def test_restart_refused_texts(self):
        result = self.run_flow("""
            zfoUpdateOffer.restartRefused('unsaved');
            const first = lastDialog().texts['[data-message]'].textContent;
            zfoUpdateOffer.restartRefused('others');
            console.log(JSON.stringify([first, lastDialog().texts['[data-message]'].textContent]));
        """)
        self.assertEqual(result, ["Nejdřív uložte nebo zavřete rozpracované dokumenty.",
                                  "Zavřete ostatní okna Blankety; aktualizace se nainstaluje po zavření posledního."])

    def test_manual_check_with_a_ready_update_opens_the_ready_dialog(self):
        result = self.run_flow("""
            answers.push({state: 'idle'}, {status: 'newer', ready: true, offer: %s});
            await zfoUpdateOffer.report();
            console.log(JSON.stringify({text: lastDialog().text, buttons: lastDialog().buttons}));
        """ % self.OFFER)
        self.assertIn("je připravená", result["text"][0])
        self.assertEqual(result["buttons"], ["Později", "Restartovat teď"])

    def test_one_update_dialog_at_a_time(self):
        result = self.run_flow("""
            zfoUpdateOffer(%s);
            const auto = lastDialog();
            answers.push({state: 'idle'}, {status: 'current'});
            await zfoUpdateOffer.report();
            zfoUpdateOffer.restartRefused('others');
            console.log(JSON.stringify({autoClosed: auto.closed, open: dialogs.filter(dialog => !dialog.closed).length}));
        """ % self.OFFER)
        self.assertEqual(result, {"autoClosed": True, "open": 1})

    def test_manual_check_during_a_download_joins_it(self):
        result = self.run_flow("""
            zfoUpdateOffer(%s);
            answers.push({state: 'downloading', version: '0.5.0', percent: 30}, {state: 'downloading', version: '0.5.0', percent: 31});
            await zfoUpdateOffer.report();
            const text = lastDialog().text.join('|');
            timers.shift()(); await settle();
            console.log(JSON.stringify({posted, text, after: lastDialog().text.join('|'), open: dialogs.filter(dialog => !dialog.closed).length,
                pollers: timers.length}));
        """ % self.OFFER)
        self.assertEqual(result["posted"], [{"action": "progress"}, {"action": "progress"}])
        self.assertEqual(result["text"], "Stahuji Blanketu 0.5.0… 30\u00a0%")
        self.assertEqual(result["after"], "Stahuji Blanketu 0.5.0… 31\u00a0%")
        self.assertEqual((result["open"], result["pollers"]), (1, 1))

    def test_joined_download_without_a_release_version_checks_instead(self):
        for version in ("null", "'0.5.0<img src=x>'", "'0.5'", "'0.5.0\\n1'", "5", "['0.5.0']"):
            with self.subTest(version=version):
                result = self.run_flow("""
                    answers.push({state: 'downloading', version: %s, percent: 30}, {status: 'current'});
                    await zfoUpdateOffer.report();
                    console.log(JSON.stringify({posted, message: lastDialog().texts['[data-message]'].textContent}));
                """ % version)
                self.assertEqual(result["posted"], [{"action": "progress"}, {"action": "check"}])
                self.assertEqual(result["message"], "Máte nejnovější verzi.")

    def test_manual_check_finding_a_ready_update_opens_the_ready_dialog(self):
        result = self.run_flow("""
            answers.push({state: 'ready', version: '0.5.0', percent: 100});
            await zfoUpdateOffer.report();
            console.log(JSON.stringify({buttons: lastDialog().buttons, posted}));
        """)
        self.assertEqual(result["buttons"], ["Později", "Restartovat teď"])
        self.assertEqual(result["posted"], [{"action": "progress"}])

    def test_download_cancelled_elsewhere_closes_the_dialog(self):
        result = self.run_flow(f"""
            answers.push({{state: 'downloading', percent: 5}}, {{state: 'idle'}});
            zfoUpdateOffer({self.OFFER});
            lastDialog().press('install'); await settle();
            timers.shift()(); await settle();
            console.log(JSON.stringify({{closed: lastDialog().closed}}));
        """)
        self.assertTrue(result["closed"])

    def test_pages_report_unsaved_changes(self):
        self.assertEqual(run_ui_scripts(["bridge.js"], "console.log(zfoHasUnsavedChanges());").strip(), "false")
        for name, expected in (("shell.js", "window.zfoHasUnsavedChanges = () => changed;"), ("pdf.mjs", "window.zfoHasUnsavedChanges = () => changed;")):
            self.assertIn(expected, ui_source(name))


class ReadyUpdateIsOfferedAgain(WithOwnSettingsFolder):
    def test_manual_check_answers_ready_without_asking_github(self):
        with mock.patch.object(update_install, "ready_for_this_app", return_value={"version": "0.5.0"}), \
                mock.patch.object(update_check, "fetch_latest", side_effect=AssertionError("network")):
            answer = update_check.check_now("0.4.0")
        self.assertEqual(answer["status"], "newer")
        self.assertIs(answer["ready"], True)
        self.assertEqual(answer["offer"]["version"], "0.5.0")

    def test_manual_check_without_a_ready_update_has_no_flag(self):
        with mock.patch.object(update_install, "ready_for_this_app", return_value=None), \
                mock.patch.object(update_check, "fetch_latest", return_value={"version": "0.5.0", "assets": {}}):
            answer = update_check.check_now("0.4.0")
        self.assertNotIn("ready", answer)


@needs_node
class AboutWindow(unittest.TestCase):
    SCRIPT = """
        const controls = {};
        const node = () => ({style: {}, append() {}, addEventListener() {}, close() {}, showModal() { this.shown = true; }});
        let about;
        document.createElement = () => {
            const created = node();
            created.querySelector = selector => (controls[selector] ??= {...node(), textContent: '', checked: false});
            return (about ??= created);
        };
        zfoAbout();
        console.log(JSON.stringify({shown: about.shown, html: about.innerHTML, wired: Object.keys(controls)}));
    """

    def test_about_dialog_opens_without_settings(self):
        result = json.loads(run_ui_scripts(["bridge.js"], self.SCRIPT))
        self.assertTrue(result["shown"])
        for fragment in ("data-update-check", "Automaticky kontrolovat", "Zkontrolovat aktualizace"):
            self.assertNotIn(fragment, result["html"])
        for fragment in ("Nahlásit chybu…", "Podpořit Blanketu…", "Zavřít"):
            self.assertIn(fragment, result["html"])


@needs_node
class ManualCheckInThePage(unittest.TestCase):
    def run_check(self, answer):
        script = """
            const dialogs = [];
            document.createElement = () => {
                const created = {style: {}, append() {}, addEventListener() {}, close() {}, showModal() { this.shown = true; },
                                 querySelector: () => ({textContent: '', append() {}, addEventListener() {}})};
                dialogs.push(created);
                return created;
            };
            document.body.append = () => {};
            fetch = async () => (ANSWER);
            (async () => {
                await zfoUpdateOffer.report();
                console.log(JSON.stringify(dialogs.map(dialog => ({shown: dialog.shown, html: dialog.innerHTML}))));
            })();
        """.replace("ANSWER", answer)
        return json.loads(run_ui_scripts(["bridge.js"], script))

    def test_current_version_is_said_so(self):
        shown = self.run_check("{ok: true, json: async () => ({status: 'current'})}")
        self.assertEqual(len(shown), 1)
        self.assertTrue(shown[0]["shown"])
        self.assertIn("data-message", shown[0]["html"])
        self.assertIn("OK</button>", shown[0]["html"])

    def test_unreachable_server_is_unknown(self):
        shown = self.run_check("{ok: false, status: 500}")
        self.assertEqual(len(shown), 1)
        self.assertTrue(shown[0]["shown"])

    def test_newer_version_opens_the_offer(self):
        offer = "{version: '9.9', current: '1.0', news_url: 'n', download_url: 'd'}"
        shown = self.run_check("{ok: true, json: async () => ({status: 'newer', offer: %s})}" % offer)
        opened = [dialog for dialog in shown if dialog.get("shown")]
        self.assertEqual(len(opened), 1)
        self.assertIn("Stáhnout…", opened[0]["html"])

    def test_texts(self):
        source = ui_source("bridge.js")
        self.assertIn("'Máte nejnovější verzi.'", source)
        self.assertIn("'Novou verzi teď nešlo zjistit. Zkuste to později.'", source)


class TheSuiteStaysOffline(unittest.TestCase):
    def test_rendering_a_page_neither_reaches_the_network_nor_the_real_settings(self):
        with mock.patch.object(update_check.public_services, "opener", side_effect=AssertionError("network")) as opener, \
                mock.patch.object(update_check.threading, "Thread", side_effect=AssertionError("thread")):
            data = page_data(app.render_empty_window())
        opener.assert_not_called()
        self.assertIn("update_offer", data)
        self.assertNotIn(str(Path.home()), str(update_check.certstore.config_dir()))
