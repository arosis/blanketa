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

DS = zfo_verify.DS
FM = "http://software602.cz/forms"
SIGNED = ROOT / "samples/mac-vyplneny/form.zfo"


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
