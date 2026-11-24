import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from support import needs_node, page_data, run_ui_scripts, ui_source

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

    def test_settings_page_is_served_with_its_script(self):
        status, body = self.request("GET", "/settings.js")
        self.assertEqual(status, 200)
        self.assertEqual(body, ui_source("settings.js"))

    def test_the_page_is_served_whatever_its_query(self):
        status, html = self.request("GET", "/?form=x")
        self.assertEqual(status, 200)
        self.assertIn("data-update-checks", html)

    def test_other_paths_answer_404_and_open_no_session(self):
        before = dict(app.SESSIONS)
        self.assertEqual(self.request("GET", "/favicon.ico")[0], 404)
        self.assertEqual(self.request("GET", "/anything?form=x")[0], 404)
        self.assertEqual(app.SESSIONS, before)

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


class TheSettingsPage(unittest.TestCase):
    LABELS = (
        "Aktualizace", "Automaticky kontrolovat nové verze",
        "Hlášení chyb", "Po chybě nabídnout hlášení při dalším otevření okna",
        "Podpora", "Občas připomenout možnost přispět na vývoj",
        "Časová razítka (podpis PDF)", "Veřejná služba Sectigo", "Vlastní služba", "Adresa služby",
        "Jméno (jen když ho služba vyžaduje)", "Heslo k\u00a0vlastní službě se zadává při každém podpisu.",
    )

    def test_page_has_all_sections_and_labels(self):
        html = ui_source("settings.html")
        for label in self.LABELS:
            with self.subTest(label=label):
                self.assertIn(label, html)

    def test_script_is_listed_among_the_page_scripts(self):
        self.assertIn("/settings.js", app.PAGE_SCRIPTS)


STORED = {"update_checks": True, "crash_offers": True, "support_reminder": True,
          "time_stamp": {"service": "public", "url": "", "user": "", "usable": True}}

FAKE_PAGE = """
    const listeners = {};
    const control = (extra = {}) => ({hidden: false, textContent: '', value: '', checked: false,
        addEventListener(type, handler) { (listeners[this.id] ??= {})[type] = handler; }, ...extra});
    const ids = {};
    const named = (id, extra) => (ids[id] = control({id, ...extra}));
    const nodes = {
        'script[data-zfo-page]': {textContent: JSON.stringify(PAGE)},
        '[data-update-checks]': named('update_checks'), '[data-crash-offers]': named('crash_offers'),
        '[data-support-reminder]': named('support_reminder'),
        '[data-time-stamp-own]': named('own'), '[data-time-stamp-url]': named('url'), '[data-time-stamp-user]': named('user'),
        '[data-error="update_checks"]': named('e_update', {hidden: true}), '[data-error="crash_offers"]': named('e_crash', {hidden: true}),
        '[data-error="support_reminder"]': named('e_support', {hidden: true}), '[data-error="time_stamp"]': named('e_stamp', {hidden: true}),
    };
    const radios = [named('public', {value: 'public'}), named('own_radio', {value: 'own'})];
    document.querySelector = selector => nodes[selector];
    document.querySelectorAll = () => radios;
    const sent = [];
    fetch = async (url, options) => {
        sent.push({url, body: options.body});
        return ANSWER(options.body);
    };
    (0, eval)(SOURCE);
    const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
    const fire = (id, type) => listeners[id][type]();
"""


@needs_node
class TheSettingsScript(unittest.TestCase):
    def run_page(self, steps, answer="async () => ({ok: true, json: async () => STORED_ANSWER})", stored=STORED):
        script = f"""
            const PAGE = {json.dumps({"session": "tok", "settings": stored})};
            const SOURCE = {json.dumps(ui_source("settings.js"))};
            const STORED_ANSWER = {json.dumps(stored)};
            const ANSWER = {answer};
            {FAKE_PAGE}
            (async () => {{
                {steps}
            }})();
        """
        return json.loads(run_ui_scripts([], script))

    def test_page_shows_the_stored_values(self):
        stored = {**STORED, "update_checks": False, "time_stamp": {"service": "own", "url": "https://t.example.cz/", "user": "u", "usable": True}}
        result = self.run_page("""console.log(JSON.stringify({
            update: ids.update_checks.checked, crash: ids.crash_offers.checked, own: ids.own.hidden,
            url: ids.url.value, user: ids.user.value, radios: radios.map(radio => radio.checked)}));""", stored=stored)
        self.assertEqual(result, {"update": False, "crash": True, "own": False, "url": "https://t.example.cz/",
                                  "user": "u", "radios": [False, True]})

    def test_toggle_posts_only_its_key(self):
        result = self.run_page("""
            ids.update_checks.checked = false;
            fire('update_checks', 'change');
            await pause(10);
            console.log(JSON.stringify(sent));""")
        self.assertEqual(result, [{"url": "/settings?s=tok", "body": '{"update_checks":false}'}])

    def test_public_service_posts_the_whole_time_stamp(self):
        stored = {**STORED, "time_stamp": {"service": "own", "url": "https://t.example.cz/", "user": "u", "usable": True}}
        result = self.run_page("""
            radios[0].checked = true; radios[1].checked = false;
            fire('public', 'change');
            await pause(10);
            console.log(JSON.stringify(sent.map(call => JSON.parse(call.body))));""", stored=stored)
        self.assertEqual(result, [{"time_stamp": {"service": "public", "url": "https://t.example.cz/", "user": "u"}}])

    def test_own_service_without_an_address_only_opens_the_fields(self):
        result = self.run_page("""
            radios[0].checked = false; radios[1].checked = true;
            fire('own_radio', 'change');
            await pause(10);
            console.log(JSON.stringify({sent: sent.length, hidden: ids.own.hidden}));""")
        self.assertEqual(result, {"sent": 0, "hidden": False})

    def test_typing_is_posted_once_after_a_pause(self):
        result = self.run_page("""
            radios[0].checked = false; radios[1].checked = true;
            fire('own_radio', 'change');
            for (const text of ['h', 'ht', ' https://t.example.cz/ ']) { ids.url.value = text; fire('url', 'input'); await pause(50); }
            const before = sent.length;
            await pause(500);
            console.log(JSON.stringify({before, sent: sent.map(call => JSON.parse(call.body))}));""")
        self.assertEqual(result["before"], 0)
        self.assertEqual(result["sent"], [{"time_stamp": {"service": "own", "url": "https://t.example.cz/", "user": ""}}])

    def test_failed_save_restores_and_explains(self):
        answer = "async () => ({ok: false, text: async () => 'Adresa služby musí začínat http:// nebo https://.'})"
        result = self.run_page("""
            ids.update_checks.checked = false;
            fire('update_checks', 'change');
            await pause(10);
            console.log(JSON.stringify({checked: ids.update_checks.checked, text: ids.e_update.textContent, hidden: ids.e_update.hidden}));""",
                               answer=answer)
        self.assertEqual(result, {"checked": True, "text": "Adresa služby musí začínat http:// nebo https://.", "hidden": False})

    def test_failed_time_stamp_save_shows_the_reason_under_the_section_and_restores_the_service(self):
        answer = "async () => ({ok: false, text: async () => 'Adresa je špatně.'})"
        result = self.run_page("""
            radios[0].checked = false; radios[1].checked = true;
            ids.url.value = 'ftp://x';
            fire('own_radio', 'change');
            await pause(10);
            console.log(JSON.stringify({text: ids.e_stamp.textContent, radios: radios.map(radio => radio.checked), url: ids.url.value}));""",
                               answer=answer)
        self.assertEqual(result, {"text": "Adresa je špatně.", "radios": [True, False], "url": ""})

    def test_next_success_clears_the_message(self):
        result = self.run_page("""
            let fail = true;
            ANSWER_NOW = () => fail;
            ids.update_checks.checked = false;
            fire('update_checks', 'change');
            await pause(10);
            const shown = ids.e_update.hidden;
            fail = false;
            fire('update_checks', 'change');
            await pause(10);
            console.log(JSON.stringify({shown, hidden: ids.e_update.hidden}));""",
                               answer="""async () => ANSWER_NOW() ? {ok: false, text: async () => 'Chyba.'} : {ok: true, json: async () => STORED_ANSWER}""")
        self.assertEqual(result, {"shown": False, "hidden": True})
