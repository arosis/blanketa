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
