"""Attacks a hostile form or web page could try, checked against the server, the verifier and the hosts' guards.

    python -I tests/test_security.py
"""
import base64
import copy
import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import quoteattr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ZFO_OFFLINE"] = "1"  # tests never download trusted lists

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from lxml import etree  # noqa: E402

import app  # noqa: E402
import certstore  # noqa: E402
import desktop  # noqa: E402
import zfo_verify  # noqa: E402
from zfoengine import api as engine  # noqa: E402
from zfoengine import form_scripts  # noqa: E402

DS = zfo_verify.DS
FM = "http://software602.cz/forms"
SIGNED = ROOT / "samples/mac-vyplneny/form.zfo"
OUTSIDE_FILE_TEXT = "OBSAH-CIZIHO-SOUBORU"


XSL_STYLESHEET = '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:exsl="http://exslt.org/common" ' \
                 'extension-element-prefixes="exsl" version="1.0"><xsl:output method="text"/>%s</xsl:stylesheet>'

FORM_WITH_CALCULATION = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:template name="sablona">%s</fm:template><fm:model><fm:instance><d:root><d:e a="puvodni"/></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="e" nodeset="d:e"><fm:bind Id="a" nodeset="@a" calculate='%s'/></fm:bind></fm:bind>
</fm:model></fm:formparams></fo:root></dsig:Object></dsig:Signature>"""


def form_root(path):
    return etree.fromstring(zipfile.ZipFile(path).read("form.fo"), etree.XMLParser(huge_tree=True))


def write_form(root, source, target):
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(target, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, etree.tostring(root) if info.filename == "form.fo" else zin.read(info.filename))


class PageInjection(unittest.TestCase):
    def test_script_texts_cannot_end_the_script(self):
        self.assertNotIn("<", app.json_for_script({"title": "</script><script>alert(1)</script>"}))
        self.assertEqual(json.loads(app.json_for_script({"t": "</script>&"})), {"t": "</script>&"})

    def test_font_that_closes_the_style_block_is_left_out(self):
        root = form_root(ROOT / "samples/cuzk/form.zfo")
        fonts = etree.SubElement(root.find(f".//{{{FM}}}formparams").getparent(), f"{{{FM}}}fonts")
        etree.SubElement(fonts, f"{{{FM}}}font", name="X").text = "AAAA)}</style><script>window.pwned=1</script>"
        etree.SubElement(fonts, f"{{{FM}}}font", name="</style><script>").text = "AAAA"
        etree.SubElement(fonts, f"{{{FM}}}font", name="Good Font").text = "AAAA"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "font.zfo"
            write_form(root, ROOT / "samples/cuzk/form.zfo", path)
            app.FORMS["font-test"] = path
            try:
                html = app.render_zfo_page("font-test")
            finally:
                del app.FORMS["font-test"]
        self.assertNotIn("window.pwned", html)
        self.assertNotIn('"</style>', html)
        self.assertIn('font-family: "Good Font"', html)


class FormExpressions(unittest.TestCase):
    """What an XPath expression written in a form can reach while the form is recalculated."""

    def recalculated_field(self, calculation, template="<a/>"):
        doc = engine.open_document((FORM_WITH_CALCULATION % (template, calculation)).encode())
        doc.opened_from_path = "/Users/nekdo/Dokumenty/formular.zfo"
        engine.handle(doc, "recalc_form", "")
        return doc._get_data_root().getroot()[0]

    def test_nothing_of_the_computer_gets_into_the_data(self):
        for call in ("GetEnvVariable(HOME)", "CurrentUserName()", "CurrentFileName()"):
            with self.subTest(call):
                self.assertEqual(self.recalculated_field(f'"{call}"').get("a"), "")

    def test_calculation_cannot_set_other_attributes_of_the_data(self):
        self.assertIsNone(self.recalculated_field('"SetAttribute(uniklo, ano)"').get("uniklo"))

    def test_calculation_cannot_save_the_form(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "ulozeno.zfo"
            for call in ("Save", "SaveSigned", "SaveAs", "SaveAsSigned"):
                with self.subTest(call):
                    with mock.patch("zfoengine.zfo_doc.ZFO_document.save_to_file") as save_to_file:
                        self.recalculated_field(f'"{call}({target})"')
                    save_to_file.assert_not_called()

    def test_calculation_cannot_send_an_application_event(self):
        self.assertEqual(self.recalculated_field('"SendAppEvent(saveformsign)"').get("a"), "")

    def test_button_can_still_send_an_application_event(self):
        doc = engine.open_document((FORM_WITH_CALCULATION % ("<a/>", "1")).encode())
        engine.handle(doc, "recalc_form", "")
        self.assertEqual(doc.perform_click_action("SendAppEvent(checkdata)")[0].tag, "checkdata")
        self.assertFalse(doc.user_action_running)

    def form_whose_root_names_a_field(self):
        doc = engine.open_document((FORM_WITH_CALCULATION % ("<a/>", "")).encode())
        root = doc._get_data_root().getroot()
        root.set("form602bindref", "e")
        return doc, root, root[0]

    def test_calculation_cannot_store_values(self):
        doc, root, field = self.form_whose_root_names_a_field()
        root.xpath("SetMultipleDataTo('a', 'vlozeno')")
        root.xpath("SetValueToData('vlozeno')")
        self.assertEqual(field.get("a"), "puvodni")
        self.assertIsNone(field.text)

    def test_button_can_still_store_values(self):
        doc, _, field = self.form_whose_root_names_a_field()
        doc.perform_click_action("SetMultipleDataTo(a, vlozeno)")
        doc.perform_click_action("SetValueToData(vlozeno)")
        self.assertEqual(field.get("a"), "vlozeno")
        self.assertEqual(field.text, "vlozeno")

    def test_failed_button_does_not_open_the_way_for_calculations(self):
        doc = engine.open_document((FORM_WITH_CALCULATION % ("<a/>", "1")).encode())
        with self.assertRaises(ValueError):
            doc.perform_click_action("RCCheck(neni-cislo)")
        self.assertFalse(doc.user_action_running)

    def test_form_template_cannot_read_a_file(self):
        with tempfile.TemporaryDirectory() as folder:
            secret = Path(folder) / "tajne.xml"
            secret.write_text("<x>TAJNE</x>")
            template = XSL_STYLESHEET % f'<xsl:template match="/"><xsl:value-of select="document(&apos;{secret.as_uri()}&apos;)"/></xsl:template>'
            field = self.recalculated_field('"DataTransform(sablona, /d:root)"', template)
        self.assertNotIn("TAJNE", field.get("a"))

    def test_form_template_cannot_write_a_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "zapsano.txt"
            template = XSL_STYLESHEET % f'<xsl:template match="/"><exsl:document href="{target}" method="text">X</exsl:document></xsl:template>'
            self.recalculated_field('"DataTransform(sablona, /d:root)"', template)
            self.assertFalse(target.exists())

    def test_form_template_cannot_import_a_file(self):
        with tempfile.TemporaryDirectory() as folder:
            imported = Path(folder) / "import.xsl"
            imported.write_text(XSL_STYLESHEET % '<xsl:template match="/">TAJNE</xsl:template>')
            template = f'<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0"><xsl:import href="{imported.as_uri()}"/></xsl:stylesheet>'
            field = self.recalculated_field('"DataTransform(sablona, /d:root)"', template)
        self.assertNotIn("TAJNE", field.get("a"))

    def test_template_working_only_with_the_form_data_still_transforms(self):
        template = XSL_STYLESHEET % '<xsl:template match="/"><xsl:value-of select="count(//*)"/></xsl:template>'
        self.assertNotIn(self.recalculated_field('"DataTransform(sablona, /d:root)"', template).get("a"), ("", "puvodni"))

    def test_public_templates_transform_as_without_the_limit(self):
        fo = zipfile.ZipFile(ROOT / "samples/cssz-onz/form.zfo").read("form.fo")
        doc = engine.open_document(fo)
        engine.handle(doc, "recalc_form", "")
        data = doc._get_data_root().getroot()
        for name in ("ico", "nvs", "rc2birthdate_uprav", "vs"):
            with self.subTest(name):
                unlimited = str(etree.XSLT(doc.get_template_text(name))(data))
                self.assertEqual(data.xpath(f"DataTransform('{name}', '.')"), unlimited)


class SignatureWrapping(unittest.TestCase):
    def test_untouched_form_verifies(self):
        self.assertTrue(all(layer["intact"] for layer in zfo_verify.describe_signature_layers(form_root(SIGNED))))

    def test_pristine_copy_in_keyinfo_does_not_hide_a_change(self):
        root = form_root(SIGNED)
        root.find(f"{{{DS}}}KeyInfo").append(copy.deepcopy(root.find(f"{{{DS}}}Object")))
        changed = [e for e in root.find(f"{{{DS}}}Object").iter() if (e.text or "").strip() == "Testovič"]
        self.assertTrue(changed)
        for e in changed:
            e.text = "Podvrženo"
        self.assertFalse(zfo_verify.describe_signature_layers(root)[0]["intact"])

    def test_reference_outside_the_layer_is_refused(self):
        root = form_root(SIGNED)
        obj = root.find(f"{{{DS}}}Object")
        self.assertIs(zfo_verify.signed_object(root, "#" + obj.get("Id")), obj)
        self.assertIsNone(zfo_verify.signed_object(root, "#nonexistent"))
        self.assertIsNone(zfo_verify.signed_object(root, "#x' or '1'='1"))
        inner = obj.find(f"{{{DS}}}Signature")
        self.assertIsNone(zfo_verify.signed_object(inner, "#" + obj.get("Id")))


class DamagedSignatures(unittest.TestCase):
    """A form whose signature layer is damaged shows a broken signature or is refused; nothing else may happen."""

    def damaged(self, change):
        root = form_root(SIGNED)
        change(root.find(f".//{{{zfo_verify.DS}}}SignedInfo"), root)
        return zfo_verify.describe_signature_layers(root)

    def test_unknown_digest_algorithm_is_a_broken_signature(self):
        def change(info, _):
            info.find(f".//{{{zfo_verify.DS}}}DigestMethod").set("Algorithm", "urn:unknown")
        self.assertFalse(self.damaged(change)[0]["intact"])

    def test_missing_signed_info_is_a_broken_signature(self):
        def change(info, _):
            info.getparent().remove(info)
        self.assertFalse(self.damaged(change)[0]["intact"])

    def test_certificate_with_an_unreadable_name_refuses_the_form(self):
        def change(_, root):
            element = root.find(f".//{{{zfo_verify.DS}}}X509Certificate")
            der = base64.b64decode(element.text)
            # The string type of each common name becomes one a name cannot have, which is noticed only when it is read.
            damaged = re.sub(rb"(\x06\x03\x55\x04\x03)[\x0c\x13]", lambda found: found[1] + b"\x01", der)
            self.assertNotEqual(damaged, der)
            element.text = base64.b64encode(damaged).decode()
        with self.assertRaisesRegex(ValueError, "readable certificate"):
            self.damaged(change)

    def test_unreadable_certificate_refuses_the_form(self):
        def change(_, root):
            root.find(f".//{{{zfo_verify.DS}}}X509Certificate").text = "not base64"
        with self.assertRaises(ValueError):
            self.damaged(change)


FORM_WITH_SCRIPT = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:a>puvodni</d:a><d:soucet/><d:sekce><d:b/></d:sekce></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="a" nodeset="d:a"/><fm:bind Id="soucet" nodeset="d:soucet" depends="a" calculate="concat(../d:a, '!')"/>
<fm:bind Id="sekce" nodeset="d:sekce"><fm:bind Id="b" nodeset="d:b"/></fm:bind></fm:bind></fm:model>
<fm:script name="knihovna" usage="1"><![CDATA[%s]]></fm:script></fm:formparams>
<fo:block form602ct="rep" form602bind="sekce" form602id="s"><fo:inline form602ct="txt" form602bind="b"/></fo:block>
<fo:inline form602ct="btn" form602bind="a"><![CDATA[Tlacitko]]></fo:inline>
</fo:root></dsig:Object></dsig:Signature>"""
BUTTON_SCRIPT_MARK = "<fo:inline form602ct=\"btn\" form602bind=\"a\">"


def form_running(script, library=""):
    button = BUTTON_SCRIPT_MARK.replace(">", f" form602btnclick={quoteattr('{' + script + '}')}>")
    return engine.open_document((FORM_WITH_SCRIPT % library).replace(BUTTON_SCRIPT_MARK, button).encode())


def click(doc, answers=()):
    return form_scripts.run_button_script(doc, 0, "", list(answers))


def value_of_a(doc):
    return doc.get_value("a")


WRITE = "GetDocument().GetElement().SetDataTo('a', 'zmeneno');"


class HostileFormScripts(unittest.TestCase):
    def test_what_reaches_outside_the_form_stops_the_script_and_undoes_it(self):
        for call in ("GetDocument().SendData(1, 2)", "GetDocument().SendDataToEPortal(1)", "GetDocument().SaveData(1)", "GetDocument().Print2PDF(1)",
                     "GetDocument().PrepareSignature(1)", "GetDocument().FinalizeSignature(1)", "GetDocument().ChooseCert()",
                     "GetDocument().Close()", "GetDocument().CloseApp()", "GetDocument().OpenAux('x')", "GetDocument().LoadFormData(1)",
                     "GetDocument().GetElement().OpenLink('http://example.org')", "GetDocument().GetElement().SetBinData('.', 1)",
                     "new SendParam()", "new XMLHttpRequest()", "eval('1')", "require('os')", "__import__('os')"):
            with self.subTest(call):
                doc = form_running(WRITE + call + ";")
                result = click(doc)
                self.assertIn("refused", result)
                self.assertEqual(value_of_a(doc), "puvodni")
                self.assertFalse(doc.get_value("soucet"))

    def test_a_script_cannot_catch_being_refused(self):
        doc = form_running(WRITE + "try { GetDocument().SendData(1); } catch (e) { GetDocument().GetElement().SetDataTo('b', 'x'); }")
        self.assertIn("refused", click(doc))
        self.assertEqual(value_of_a(doc), "puvodni")

    def test_a_script_gets_no_python_object(self):
        for reach in ("GetDocument.__class__", "GetDocument().__class__", "GetDocument().GetElement.__globals__", "GetDocument().GetElement().constructor"):
            with self.subTest(reach):
                result = click(form_running(f"var x = {reach}; if (x) GetDocument().SendData(x);"))
                self.assertTrue(result is not None and ("refused" in result or result["values"] == {}))

    def test_endless_loop_of_writes_stops_and_is_undone(self):
        doc = form_running("while (true) { GetDocument().GetElement().SetDataTo('a', 'x'); }")
        self.assertIn("refused", click(doc))
        self.assertEqual(value_of_a(doc), "puvodni")

    def test_calculations_inside_a_script_do_not_renew_its_budget(self):
        doc = form_running("var i = 0; while (true) { i = i + 1; if (i % 1000 == 0) GetDocument().GetElement().SetDataTo('a', '' + i); }")
        self.assertIn("refused", click(doc))

    def test_growing_data_stops(self):
        doc = form_running("var s = 'x'; for (var i = 0; i < 30; i++) s = s + s; GetDocument().GetElement().SetDataTo('a', s);")
        self.assertIn("refused", click(doc))
        self.assertEqual(value_of_a(doc), "puvodni")

    def test_copies_of_a_section_stop_growing(self):
        doc = form_running("while (true) GetDocument().GetElement().InsertSection('//*[@form602id=\"s\"]');")
        self.assertIn("refused", click(doc))
        self.assertEqual(len(doc._get_data_root().getroot().findall("{http://software602.cz/sample}sekce")), 1)

    def test_dialogs_stop_flooding(self):
        self.assertIn("refused", click(form_running("while (true) MessageBox('x');")))

    def test_questions_stop_repeating(self):
        script = "while (true) MessageBox('x', 'y', MB_YESNO);"
        doc = form_running(script)
        answers = []
        for _ in range(form_scripts.MAX_QUESTIONS_PER_RUN):
            self.assertIn("question", click(doc, answers))
            answers.append(True)
        self.assertIn("refused", click(doc, answers))

    def test_kept_functions_refuse_in_later_calculations_and_runs(self):
        doc = form_running("kept = GetDocument().GetElement();")
        self.assertNotIn("refused", click(doc))
        interpreter = doc.script_interpreter()
        with self.assertRaises(form_scripts.js602.Unsupported):
            form_scripts.js602.evaluate(interpreter, "{ kept.SetDataTo('a', 'z'); }", lambda expression: "", "", lambda *args: "")
        self.assertEqual(value_of_a(doc), "puvodni")

    def test_kept_functions_refuse_in_a_nested_calculation(self):
        form = (FORM_WITH_SCRIPT % "").replace("calculate=\"concat(../d:a, '!')\"", "calculate=\"{ var x = 1; kept.SetDataTo('a', 'z'); Result('c'); }\"")
        form = form.replace(BUTTON_SCRIPT_MARK, BUTTON_SCRIPT_MARK.replace(">", " form602btnclick=\"{ kept = GetDocument().GetElement(); kept.SetDataTo('a', 'y'); }\">"))
        doc = engine.open_document(form.encode())
        click(doc)
        self.assertEqual(value_of_a(doc), "y")

    def test_xpath_cannot_reach_application_functions(self):
        doc = form_running("GetDocument().GetElement().DataXpath('SendAppEvent(\"store\")');")
        self.assertIn("refused", click(doc))

    def test_script_guard_comes_back_after_a_calculation(self):
        doc = form_running(WRITE + "GetDocument().GetElement().DataXpath('SendAppEvent(\"store\")');")
        self.assertIn("refused", click(doc))
        self.assertFalse(doc.script_running)

    def test_page_cannot_hand_over_a_script_of_its_own(self):
        doc = form_running(WRITE)
        for message in ({"button": 99}, {"button": -1}, {"field": "a') or ('"}, {"field": {"x": 1}}):
            answers = engine.handle(doc, "run_form_script", message)
            self.assertFalse([answer for answer in answers if "form_script_callback" in str(answer.get("data"))], message)
        self.assertEqual(value_of_a(doc), "puvodni")
        engine.handle(doc, "run_form_script", {"button": 0, "command": "{ GetDocument().GetElement().SetDataTo('a', 'jine'); }"})
        self.assertEqual(value_of_a(doc), "zmeneno")

    def test_message_reaches_the_page_only_as_json_data(self):
        doc = form_running("MessageBox('</script><img src=x onerror=alert(1)>\\u2028\"); alert(1); (\"');")
        answer = engine.handle(doc, "run_form_script", {"button": 0})[0]["data"]
        arguments = answer[len("form_script_callback("):-1]
        self.assertIn("onerror", json.loads(arguments)["messages"][0]["text"])

    def test_page_shows_messages_only_as_text(self):
        self.assertIn("const plainTextOf = markup => new DOMParser()", app.SHELL)
        callback = app.SHELL[app.SHELL.index("window.form_script_callback"):app.SHELL.index("const icons = {")]
        self.assertNotIn("innerHTML", callback)

    def test_opening_the_form_runs_no_script(self):
        doc = form_running("GetDocument().SendData(1);", library="kept_at_open = 1;")
        self.assertEqual(value_of_a(doc), "puvodni")
        self.assertNotIn("kept_at_open", doc.script_interpreter().globals.vars)

    def test_value_only_loaded_by_the_page_runs_no_change_script(self):
        form = (FORM_WITH_SCRIPT % "").replace(BUTTON_SCRIPT_MARK, BUTTON_SCRIPT_MARK.replace(">", " form602onchange=\"{ GetDocument().GetElement().SetDataTo('b', 'spusteno'); }\">"))
        doc = engine.open_document(form.encode())
        engine.handle(doc, "store_val", {"field": "a", "value": "puvodni", "input_id": "x", "repe": ""})
        self.assertNotEqual(doc.get_value("b"), "spusteno")
        engine.handle(doc, "store_val", {"field": "a", "value": "nove", "input_id": "x", "repe": ""})
        self.assertEqual(doc.get_value("b"), "spusteno")


class LocalServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        app.FORMS["cuzk-test"] = ROOT / "samples/cuzk/form.zfo"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        del app.FORMS["cuzk-test"]

    def request(self, method, path, host=None, origin=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if origin:
            headers["Origin"] = origin
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()

    def test_own_pages_load(self):
        self.assertEqual(self.request("GET", "/?form=cuzk-test")[0], 200)
        self.assertEqual(self.request("GET", "/?form=cuzk-test", host=f"localhost:{self.port}")[0], 200)

    def test_rebound_host_is_refused(self):
        self.assertEqual(self.request("GET", "/?form=cuzk-test", host=f"attacker.example:{self.port}")[0], 403)
        self.assertEqual(self.request("GET", "/identities", host="attacker.example")[0], 403)

    def test_post_from_another_origin_is_refused(self):
        status, page = self.request("GET", "/?form=cuzk-test")
        session = page.decode().split("const SESSION = '")[1].split("'")[0]
        self.assertEqual(self.request("POST", f"/export-data?s={session}")[0], 200)
        self.assertEqual(self.request("POST", f"/export-data?s={session}", origin=f"http://127.0.0.1:{self.port}")[0], 200)
        self.assertEqual(self.request("POST", f"/export-data?s={session}", origin="https://attacker.example")[0], 403)

    def test_revocation_answers_only_about_signatures_shown(self):
        session = self.request("GET", "/?form=cuzk-test")[1].decode().split("const SESSION = '")[1].split("'")[0]
        known = next(iter(app.SHOWN_CERTIFICATES_BY_ID))
        self.assertEqual(self.request("POST", "/revocation", body=json.dumps([known]))[0], 404)
        status, answer = self.request("POST", f"/revocation?s={session}", body=json.dumps([known, "unknown-id", 5]))
        self.assertEqual(status, 200)
        self.assertEqual(list(json.loads(answer)), [known])
        self.assertEqual(self.request("POST", f"/revocation?s={session}", body="not json")[0], 400)
        self.assertEqual(self.request("POST", f"/revocation?s={session}", origin="https://attacker.example", body="[]")[0], 403)

    def test_damaged_file_gets_a_message(self):
        with tempfile.TemporaryDirectory() as folder:
            damaged = Path(folder) / "poskozeny.pdf"
            damaged.write_bytes(b"%PDF-1.7\nnot a document")
            app.FORMS["damaged-test"] = damaged
            try:
                status, page = self.request("GET", "/?form=damaged-test")
            finally:
                del app.FORMS["damaged-test"]
        self.assertEqual(status, 422)
        self.assertIn("nelze otevřít", page.decode())

    def test_static_files_stay_in_their_folders(self):
        self.assertEqual(self.request("GET", "/static/scripts/zfo_script.js")[0], 200)
        for path in ("/static/../../../../app.py", "/pdfjs/../../app.py", "/static/..%2f..%2fapp.py", "/pdfjs//etc/passwd"):
            self.assertEqual(self.request("GET", path)[0], 404, path)

    def test_certificate_file_needs_a_session(self):
        body = json.dumps({"data": "", "password": ""})
        self.assertEqual(self.request("POST", "/add-certificate", body=body)[0], 404)

    def serve_file(self, content, suffix=".fo"):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / f"hostile{suffix}"
        path.write_bytes(content)
        app.FORMS["hostile"] = path
        self.addCleanup(app.FORMS.pop, "hostile", None)
        return self.request("GET", "/?form=hostile")

    def test_damaged_files_end_in_a_message(self):
        original = zipfile.ZipFile(ROOT / "samples/cuzk/form.zfo").read("form.fo")
        for content in (b"", b"not xml", b"<html><body>page</body></html>", original[:len(original) // 2],
                        b"PK\x03\x04 broken archive", b"\xff\xfe\x00garbage"):
            with self.subTest(content=content[:20]):
                status, page = self.serve_file(content)
                self.assertEqual(status, 422)
                self.assertIn("Soubor nelze otevřít".encode(), page)

    def test_external_entity_is_not_read_into_the_page(self):
        with tempfile.TemporaryDirectory() as folder:
            outside_file = Path(folder) / "outside.txt"
            outside_file.write_text(OUTSIDE_FILE_TEXT, encoding="utf-8")
            original = zipfile.ZipFile(ROOT / "samples/cuzk/form.zfo").read("form.fo").decode("utf-8")
            declaration, body = original.split("?>", 1)
            hostile = f'{declaration}?><!DOCTYPE x [<!ENTITY e SYSTEM "{outside_file.as_uri()}">]>{body}'
            hostile = hostile.replace("</dsig:Signature>", "&e;</dsig:Signature>", 1).encode("utf-8")
            status, _ = self.serve_file(hostile)
            self.assertEqual(status, 422)
            with self.assertRaises(etree.XMLSyntaxError):
                app.parse_fo(hostile)
            with self.assertRaises(etree.XMLSyntaxError):
                zfo_verify.load_form_root(app.FORMS["hostile"])

    def test_entity_bomb_is_refused(self):
        entities = '<!ENTITY a0 "xxxxxxxxxx">' + "".join(f'<!ENTITY a{i} "{f"&a{i - 1};" * 10}">' for i in range(1, 10))
        status, _ = self.serve_file(f'<?xml version="1.0"?><!DOCTYPE l [{entities}]><l>&a9;</l>'.encode())
        self.assertEqual(status, 422)

    def test_changed_form_shows_a_broken_signature(self):
        root = etree.fromstring(zipfile.ZipFile(ROOT / "samples/cuzk/form.zfo").read("form.fo"), etree.XMLParser(huge_tree=True))
        layout_block = root.find(".//{http://www.w3.org/1999/XSL/Format}block")
        layout_block.set("changed", "1")
        status, page = self.serve_file(etree.tostring(root))
        self.assertEqual(status, 200)
        signatures = json.loads(re.search(r"let signatures = (\[.*?\]);\n", page.decode()).group(1))
        self.assertTrue(signatures and not all(signature["intact"] for signature in signatures))


class CertificateIds(unittest.TestCase):
    def test_identity_id_cannot_name_another_file(self):
        for bad in ("file:../../../etc/passwd", "file:" + "a" * 39, "test:../user", "file:"):
            with self.assertRaises(LookupError, msg=bad):
                certstore.load_identity(bad, "x")
        with self.assertRaises(LookupError):
            certstore.remove_pfx("../certificates")


class Sessions(unittest.TestCase):
    def test_old_sessions_are_dropped(self):
        before = dict(app.SESSIONS)
        try:
            tokens = [app.new_session({"name": str(n)}) for n in range(app.MAX_SESSIONS + 3)]
            self.assertLessEqual(len(app.SESSIONS), app.MAX_SESSIONS)
            self.assertIn(tokens[-1], app.SESSIONS)
            self.assertNotIn(tokens[0], app.SESSIONS)
        finally:
            app.SESSIONS.clear()
            app.SESSIONS.update(before)


class HostGuards(unittest.TestCase):
    def test_only_print_files_go_to_the_system(self):
        with tempfile.NamedTemporaryFile(prefix="zfo-tisk-", suffix=".pdf", dir=tempfile.gettempdir()) as f:
            self.assertTrue(desktop.is_print_file(f.name))
        with tempfile.NamedTemporaryFile(prefix="zfo-tisk-", suffix=".exe", dir=tempfile.gettempdir()) as f:
            self.assertFalse(desktop.is_print_file(f.name))
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "zfo-tisk-x.pdf"
            other.write_bytes(b"%PDF")
            self.assertFalse(desktop.is_print_file(other))
        self.assertFalse(desktop.is_print_file(sys.executable))


class LinuxTrust(unittest.TestCase):
    """The Linux verdict comes from pyhanko-certvalidator and the distribution's CA bundle; it runs anywhere one exists."""

    def setUp(self):
        self.roots = certstore._linux_system_roots()
        if not self.roots:
            self.skipTest("no system CA bundle")
        self.saved = certstore.IS_MAC, certstore.IS_WINDOWS
        certstore.IS_MAC = certstore.IS_WINDOWS = False

    def tearDown(self):
        certstore.IS_MAC, certstore.IS_WINDOWS = self.saved

    def test_system_root_is_trusted_and_test_certificate_is_not(self):
        root = next(c for c in self.roots if c.signature_hash_algorithm and c.serial_number > 0)
        test = x509.load_pem_x509_certificate((ROOT / "pki/user.crt").read_bytes())
        verdicts = certstore.trust_verdicts([root.public_bytes(serialization.Encoding.DER),
                                    test.public_bytes(serialization.Encoding.DER)])
        self.assertTrue(verdicts[0]["trusted"], verdicts[0])
        self.assertFalse(verdicts[1]["trusted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
