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

DS = zfo_verify.DS
FM = "http://software602.cz/forms"
SIGNED = ROOT / "samples/mac-vyplneny/form.zfo"
OUTSIDE_FILE_TEXT = "OBSAH-CIZIHO-SOUBORU"


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
