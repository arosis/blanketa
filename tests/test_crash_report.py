"""After a crash Blanketa offers an error report the next time a window opens."""
import http.client
import json
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from support import ROOT, isds_sample, needs_node, run_ui_scripts

import app
import crash_report
import desktop
import isds_message


def page_data(page):
    return json.loads(re.search(r"<script type=\"application/json\" data-zfo-page[^>]*>(.*?)</script>", page, re.S).group(1))


class WithOwnSettings(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = Path(folder.name) / crash_report.SETTINGS_FILE
        patcher = mock.patch.object(crash_report, "settings_path", return_value=self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)


def failing_signature():
    raise KeyError("Jan Novák 9801011234")


class CrashRecord(WithOwnSettings):
    def recorded_error(self):
        try:
            failing_signature()
        except KeyError as error:
            crash_report.record_error(error, "POST /pdf-save")

    def test_error_is_offered_once_without_its_message(self):
        self.recorded_error()
        report = crash_report.take_pending()
        self.assertIn("Chyba serveru: KeyError při POST /pdf-save", report)
        self.assertIn("tests/test_crash_report.py:", report)
        self.assertIn(" failing_signature", report)
        for private in ("Novák", "9801011234", str(Path.home())):
            self.assertNotIn(private, report)
        self.assertIsNone(crash_report.take_pending())

    def test_server_exit_is_offered(self):
        crash_report.record_server_exit(-11)
        self.assertRegex(crash_report.take_pending(), r"^Server okna nečekaně skončil \(kód -11, \d{4}-\d\d-\d\d\)\.$")

    def test_turned_off_records_nothing_until_turned_on_again(self):
        crash_report.set_enabled(False)
        crash_report.record_server_exit(1)
        self.assertFalse(crash_report.enabled())
        self.assertIsNone(crash_report.take_pending())
        crash_report.set_enabled(True)
        crash_report.record_server_exit(1)
        self.assertIsNotNone(crash_report.take_pending())

    def test_turning_off_drops_a_waiting_record(self):
        crash_report.record_server_exit(1)
        crash_report.set_enabled(False)
        crash_report.set_enabled(True)
        self.assertIsNone(crash_report.take_pending())

    def test_damaged_settings_count_as_none(self):
        for content in ("nesmysl", "[]", '{"pending": 5}', '{"pending": "x", "disabled": "ano"}'):
            with self.subTest(content=content):
                self.settings.write_text(content, encoding="utf-8")
                self.assertIsNone(crash_report.take_pending())
                self.assertTrue(crash_report.enabled())

    def test_unwritable_settings_never_break_the_server(self):
        with mock.patch.object(crash_report, "_store", side_effect=OSError("read-only")):
            crash_report.record_server_exit(1)


class CrashInTheLocalServer(WithOwnSettings):
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
        try:
            response = connection.getresponse()
            return response.status, response.read().decode()
        except (ConnectionError, http.client.RemoteDisconnected):
            return None, ""

    def test_failing_request_leaves_a_record_for_the_next_window(self):
        with mock.patch.object(app.Handler, "_store_time_stamp_settings", side_effect=KeyError("Jan Novák")):
            self.post("/time-stamp-settings")
        data = page_data(app.render_empty_window())
        self.assertIn("Chyba serveru: KeyError při POST /time-stamp-settings", data["crash_report"])
        self.assertNotIn("Novák", data["crash_report"])
        self.assertIsNone(page_data(app.render_empty_window())["crash_report"])

    def test_page_turns_the_offer_off_and_on(self):
        self.assertEqual(self.post("/crash-report", b'{"enabled": false}'), (200, '{"enabled": false}'))
        self.assertNotIn("crash_reports", page_data(app.render_empty_window()))
        self.assertEqual(self.post("/crash-report", b'{"enabled": true}'), (200, '{"enabled": true}'))

    def test_malformed_choice_is_refused(self):
        for body in (b"[", b"[]", b"{}", b'{"enabled": "ne"}'):
            with self.subTest(body=body):
                self.assertEqual(self.post("/crash-report", body)[0], 400)
        self.assertFalse(self.settings.exists())

    def test_message_and_empty_windows_may_turn_it_off(self):
        message = app.new_session({"name": "zprava", "message": isds_message.read_message(isds_sample("prijata"))})
        empty = page_data(app.render_empty_window())["session"]
        for token in (message, empty):
            with self.subTest(token=token):
                self.assertEqual(self.post("/crash-report", b'{"enabled": false}', token)[0], 200)


    def test_empty_window_reaches_nothing_but_the_choice(self):
        empty = page_data(app.render_empty_window())["session"]
        for path in ("/save", "/ipc", "/pdf-save", "/export-data", "/add-certificate", "/time-stamp-settings"):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, b"{}", empty)[0], 404)
        self.assertIsNone(crash_report.take_pending())


class HostsNoticeAServerThatStopped(WithOwnSettings):
    def form_window(self, exit_code):
        form = desktop.FormWindow.__new__(desktop.FormWindow)
        form.server = mock.Mock(**{"wait.return_value": exit_code, "poll.return_value": exit_code})
        form.server_stop_requested = False
        return form

    def test_desktop_records_a_server_that_ended_by_itself(self):
        self.form_window(3)._record_unexpected_server_exit()
        self.assertIn("kód 3", crash_report.take_pending())

    def test_desktop_ignores_a_server_it_stopped(self):
        form = self.form_window(-15)
        form.stop_server()
        form._record_unexpected_server_exit()
        self.assertIsNone(crash_report.take_pending())

    def test_mac_writes_the_same_settings_file(self):
        swift = (ROOT / "macapp/main.swift").read_text(encoding="utf-8")
        self.assertIn(f'"Library/Application Support/Blanketa/{crash_report.SETTINGS_FILE}"', swift)
        self.assertIn('stored["pending"] = ["kind": "server-exit", "exit_code": Int(code), "date": today]', swift)
        self.assertIn("if self?.serverStopRequested == false { CrashReport.recordServerExit(code: process.terminationStatus) }", swift)


@needs_node
class CrashInTheReport(unittest.TestCase):
    def test_report_carries_the_crash_record(self):
        text = json.loads(run_ui_scripts(["bridge.js"], "console.log(JSON.stringify(zfoReportError.template('Chyba serveru: KeyError')))"))
        self.assertTrue(text.endswith("Záznam o chybě z minulé práce:\nChyba serveru: KeyError\n"), text[-120:])
