import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from support import page_data

import app
import preferences
import time_stamps

OWN_SERVICE = {"service": "own", "url": "https://tsa.example.cz/tsa", "user": ""}


class InTheLocalServer(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        patcher = mock.patch.object(time_stamps.certstore, "config_dir", return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(app, "SETTINGS_ONLY", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.server.handle_error = lambda request, address: None
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.pdf_session = app.new_session({"name": "zadost", "pdf": b""})

    def request(self, method, path, body=b"{}"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request(method, path, body=body if method == "POST" else None, headers={"Host": f"127.0.0.1:{self.port}"})
        response = connection.getresponse()
        return response.status, response.read().decode()

    def settings_page(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        return page_data(html)

    def post(self, path, body, token):
        return self.request("POST", f"{path}?s={token}", json.dumps(body).encode())

    def test_settings_page_carries_all_options(self):
        data = self.settings_page()
        self.assertEqual(data["settings"], preferences.current())
        self.assertEqual(app.SESSIONS[data["session"]], {"settings": True})
        self.assertIn("app_version", data)
        self.assertIn("system", data)

    def test_every_load_has_its_own_session(self):
        self.assertNotEqual(self.settings_page()["session"], self.settings_page()["session"])

    def test_change_is_stored_and_answered(self):
        token = self.settings_page()["session"]
        status, body = self.post("/settings", {"update_checks": False, "time_stamp": OWN_SERVICE}, token)
        self.assertEqual(status, 200)
        answer = json.loads(body)
        self.assertEqual(answer, preferences.current())
        self.assertFalse(answer["update_checks"])
        self.assertEqual(time_stamps.settings()["url"], OWN_SERVICE["url"])

    def test_bad_address_answers_with_the_reason(self):
        token = self.settings_page()["session"]
        status, body = self.post("/settings", {"time_stamp": {"service": "own", "url": "ftp://x"}}, token)
        self.assertEqual(status, 400)
        self.assertIn("http://", body)

    def test_unwritable_settings_answer_with_an_error(self):
        token = self.settings_page()["session"]
        with mock.patch.object(preferences, "change", side_effect=OSError("read-only")):
            status, body = self.post("/settings", {"update_checks": False}, token)
        self.assertEqual((status, body), (400, "read-only"))

    def test_sessions_stay_in_their_lane(self):
        token = self.settings_page()["session"]
        for path in ("/save", "/pdf-save", "/time-stamp-settings", "/update-check"):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, {}, token)[0], 404)
        self.assertEqual(self.post("/settings", {}, self.pdf_session)[0], 404)

    def test_time_stamp_route_reads_without_storing(self):
        time_stamps.store_enabled(True)
        status, body = self.post("/time-stamp-settings", {}, self.pdf_session)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {**time_stamps.settings(), "usable": True})
        self.assertTrue(time_stamps.settings()["enabled"])

    def test_time_stamp_route_stores_only_enabled(self):
        body = {"enabled": True, "service": "own", "url": "https://x.example.cz/", "user": "jmeno"}
        status, answer = self.post("/time-stamp-settings", body, self.pdf_session)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(answer), {"enabled": True, "service": "public", "url": "", "user": "", "usable": True})
        self.assertEqual(time_stamps.settings(), {"enabled": True, "service": "public", "url": "", "user": ""})

    def test_time_stamp_route_refuses_other_shapes(self):
        for body in (["enabled"], {"enabled": "yes"}):
            with self.subTest(body=body):
                self.assertEqual(self.post("/time-stamp-settings", body, self.pdf_session)[0], 400)

    def test_unusable_own_service_is_reported_to_the_dialog(self):
        (self.folder / time_stamps.SETTINGS_FILE).write_text(
            json.dumps({"enabled": True, "service": "own", "url": "ftp://x", "user": ""}), encoding="utf-8")
        self.assertFalse(json.loads(self.post("/time-stamp-settings", {}, self.pdf_session)[1])["usable"])
        pdf = self.folder / "zadost.pdf"
        pdf.write_bytes(b"")
        with mock.patch.dict(app.FORMS, {"zadost": pdf}), \
                mock.patch.object(app, "pdf_signatures", return_value=[]), \
                mock.patch.object(app, "pdf_info", return_value={}):
            html = app.render_pdf_page("zadost")
        self.assertFalse(page_data(html)["time_stamp"]["usable"])
