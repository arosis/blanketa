"""Attacks a hostile form or web page could try, checked against the server, the verifier and the hosts' guards.

    python -I tests/test_security.py
"""
import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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


def form_root(path):
    return etree.fromstring(zipfile.ZipFile(path).read("form.fo"), etree.XMLParser(huge_tree=True))


def write_form(root, source, target):
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(target, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, etree.tostring(root) if info.filename == "form.fo" else zin.read(info.filename))


class PageInjection(unittest.TestCase):
    def test_script_texts_cannot_end_the_script(self):
        self.assertNotIn("<", app.js({"title": "</script><script>alert(1)</script>"}))
        self.assertEqual(json.loads(app.js({"t": "</script>&"})), {"t": "</script>&"})

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
                html = app.render("font-test")
            finally:
                del app.FORMS["font-test"]
        self.assertNotIn("window.pwned", html)
        self.assertNotIn('"</style>', html)
        self.assertIn('font-family: "Good Font"', html)


class SignatureWrapping(unittest.TestCase):
    def test_untouched_form_verifies(self):
        self.assertTrue(all(layer["intact"] for layer in zfo_verify.describe(form_root(SIGNED))))

    def test_pristine_copy_in_keyinfo_does_not_hide_a_change(self):
        root = form_root(SIGNED)
        root.find(f"{{{DS}}}KeyInfo").append(copy.deepcopy(root.find(f"{{{DS}}}Object")))
        changed = [e for e in root.find(f"{{{DS}}}Object").iter() if (e.text or "").strip() == "Testovič"]
        self.assertTrue(changed)
        for e in changed:
            e.text = "Podvrženo"
        self.assertFalse(zfo_verify.describe(root)[0]["intact"])

    def test_reference_outside_the_layer_is_refused(self):
        root = form_root(SIGNED)
        obj = root.find(f"{{{DS}}}Object")
        self.assertIs(zfo_verify.signed_object(root, "#" + obj.get("Id")), obj)
        self.assertIsNone(zfo_verify.signed_object(root, "#nonexistent"))
        self.assertIsNone(zfo_verify.signed_object(root, "#x' or '1'='1"))
        inner = obj.find(f"{{{DS}}}Signature")
        self.assertIsNone(zfo_verify.signed_object(inner, "#" + obj.get("Id")))


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
                certstore.load(bad, "x")
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
        self.roots = certstore._system_roots()
        if not self.roots:
            self.skipTest("no system CA bundle")
        self.saved = certstore.IS_MAC, certstore.IS_WINDOWS
        certstore.IS_MAC = certstore.IS_WINDOWS = False

    def tearDown(self):
        certstore.IS_MAC, certstore.IS_WINDOWS = self.saved

    def test_system_root_is_trusted_and_test_certificate_is_not(self):
        root = next(c for c in self.roots if c.signature_hash_algorithm and c.serial_number > 0)
        test = x509.load_pem_x509_certificate((ROOT / "pki/user.crt").read_bytes())
        verdicts = certstore.trust([root.public_bytes(serialization.Encoding.DER),
                                    test.public_bytes(serialization.Encoding.DER)])
        self.assertTrue(verdicts[0]["trusted"], verdicts[0])
        self.assertFalse(verdicts[1]["trusted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
