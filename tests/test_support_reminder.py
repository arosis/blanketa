"""The About window, the app's version on the pages and the reminder to support Blanketa."""
import http.client
import json
import re
import tempfile
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from support import ROOT

import app
import support_reminder

TODAY = date(2026, 11, 20)


class WithOwnSettingsFolder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = Path(folder.name) / support_reminder.SETTINGS_FILE
        patcher = mock.patch.object(support_reminder.certstore, "config_dir", return_value=Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)


class SupportReminder(WithOwnSettingsFolder):
    def test_first_signature_brings_the_reminder(self):
        self.assertTrue(support_reminder.offer(TODAY))

    def test_reminder_waits_two_weeks_after_it_was_shown(self):
        support_reminder.offer(TODAY)
        self.assertFalse(support_reminder.offer(date(2026, 12, 3)))
        self.assertTrue(support_reminder.offer(date(2026, 12, 4)))

    def test_hidden_reminder_never_comes_back(self):
        support_reminder.hide()
        self.assertFalse(support_reminder.offer(date(2027, 11, 20)))

    def test_date_in_the_future_does_not_silence_it_for_good(self):
        support_reminder.offer(date(2030, 1, 1))
        self.assertTrue(support_reminder.offer(TODAY))

    def test_damaged_settings_count_as_none(self):
        for content in ("nesmysl", '["hidden"]', '{"last_shown": 5, "hidden": "ano"}'):
            with self.subTest(content=content):
                self.settings.write_text(content, encoding="utf-8")
                self.assertTrue(support_reminder.offer(TODAY))


class SupportReminderOverTheLocalServer(WithOwnSettingsFolder):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token = app.new_session({"name": "zadost", "pdf": b""})

    def post(self, body):
        port = self.server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        connection.request("POST", f"/support-reminder?s={self.token}", body=body, headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        return response.status, response.read().decode()

    def test_page_learns_once_that_the_reminder_is_due(self):
        self.assertEqual(self.post(b'{"action": "offer"}'), (200, '{"show": true}'))
        self.assertEqual(self.post(b'{"action": "offer"}'), (200, '{"show": false}'))

    def test_page_hides_the_reminder_for_good(self):
        self.assertEqual(self.post(b'{"action": "hide"}'), (200, '{"show": false}'))
        self.assertTrue(json.loads(self.settings.read_text(encoding="utf-8"))["hidden"])

    def test_malformed_request_is_refused(self):
        self.assertEqual(self.post(b"[")[0], 400)
        self.assertEqual(self.post(b"[]")[0], 400)
        for unknown in (b"{}", b'{"action": "ukaz"}', b'{"action": null}'):
            with self.subTest(body=unknown):
                self.assertEqual(self.post(unknown)[0], 400)
        self.assertFalse(self.settings.exists())

    def test_settings_that_cannot_be_written_never_show_it(self):
        with mock.patch.object(support_reminder, "_store", side_effect=OSError("read-only")):
            self.assertEqual(self.post(b'{"action": "offer"}'), (200, '{"show": false}'))


class AboutWindow(unittest.TestCase):
    def test_every_page_knows_the_app_version(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        page = app.render_empty_window()
        data = json.loads(re.search(r"<script type=\"application/json\" data-zfo-page[^>]*>(.*?)</script>", page, re.S).group(1))
        self.assertEqual(data["app_version"], version)

    def test_version_travels_with_every_build(self):
        self.assertIn('--add-data "$PWD/VERSION:."', (ROOT / "build.sh").read_text(encoding="utf-8"))
        self.assertIn('*data("VERSION", ".")', (ROOT / "tools/build_desktop.py").read_text(encoding="utf-8"))

    def test_every_host_offers_it_in_the_menu(self):
        self.assertIn('formItem("O aplikaci Blanketa", "about")', (ROOT / "macapp/main.swift").read_text(encoding="utf-8"))
        self.assertIn('page_action("O aplikaci Blanketa", "about")', (ROOT / "desktop.py").read_text(encoding="utf-8"))

    def test_every_page_opens_it_from_the_menu(self):
        for page in ("shell.js", "pdf.mjs", "message.js", "container.js"):
            with self.subTest(page=page):
                self.assertIn("about: () => zfoAbout()", (ROOT / "ui" / page).read_text(encoding="utf-8"))
