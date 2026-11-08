"""The form description Form Filler shows, barcodes the forms draw themselves, and saving.

    python -I tests/test_app.py
"""
import base64
import copy
import http.client
import importlib.util
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from asn1crypto import cms
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree
from lxml import html as lxml_html
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["ZFO_OFFLINE"] = "1"  # tests never download trusted lists

import asic_samples  # noqa: E402

import app  # noqa: E402
import barcode  # noqa: E402
import desktop  # noqa: E402
import isds_message  # noqa: E402
import linux_print  # noqa: E402
import pdf_form_data  # noqa: E402
import pdf_sign  # noqa: E402
import zfo_verify  # noqa: E402
from zfoengine import api as engine  # noqa: E402
from zfoengine import js602  # noqa: E402

NS = {"d": "http://software602.cz/sample"}
FORM_WITH_ATTRIBUTE = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:e a=""/></d:root></fm:instance><fm:bind Id="k" nodeset="/d:root"><fm:bind Id="e" nodeset="d:e">
<fm:bind Id="a" nodeset="@a" calculate='%s'/></fm:bind></fm:bind></fm:model></fm:formparams></fo:root></dsig:Object></dsig:Signature>"""

ITF_BY_PATTERN = {pattern: digit for digit, pattern in barcode.ITF_DIGIT_PATTERNS.items()}


def decode_itf(widths):
    """Reads Interleaved 2 of 5 element widths back into digits, checking start and stop patterns."""
    assert widths[:4] == [1, 1, 1, 1] and widths[-3:] == [barcode.WIDE_MODULES, 1, 1], "start or stop pattern"
    body, digits = widths[4:-3], ""
    for i in range(0, len(body), 10):
        pair = body[i:i + 10]
        to_pattern = lambda ws: "".join("w" if w == barcode.WIDE_MODULES else "n" for w in ws)  # noqa: E731
        digits += ITF_BY_PATTERN[to_pattern(pair[0::2])] + ITF_BY_PATTERN[to_pattern(pair[1::2])]
    return digits


class Barcodes(unittest.TestCase):
    def test_interleaved_2_of_5_round_trip(self):
        for data in ("7582212456", "0000", "1234567"):
            self.assertEqual(decode_itf(barcode.element_widths_starting_with_bar(data)), data.zfill(len(data) + len(data) % 2))

    def test_svg_size_follows_the_form(self):
        drawing = barcode.barcode_svg("interleaved2of5", "7582212456", "8.0mm", "0.4mm", "not")
        self.assertIn('height="8mm"', drawing)
        self.assertNotIn("<text", drawing)
        self.assertIn("<text", barcode.barcode_svg("interleaved2of5", "12", "8mm", "0.4mm", "yes"))

    def test_unknown_symbology_falls_back_to_text(self):
        self.assertEqual(barcode.svg_data_uri("code128", "ABC"), "")
        self.assertEqual(barcode.svg_data_uri("interleaved2of5", "12A4"), "")

    def test_form_barcode_is_drawn(self):
        app.FORMS["barcode-test"] = ROOT / "samples/cssz-onz/form.zfo"
        try:
            html = app.render_zfo_page("barcode-test")
        finally:
            del app.FORMS["barcode-test"]
        self.assertIn('<img class="zfo-barcode" src="data:image/svg+xml;base64,', html)


class FormDescription(unittest.TestCase):
    def test_summary_fields_and_dates(self):
        app.FORMS["info-test"] = ROOT / "samples/olk-dotace/form.zfo"
        try:
            info = app.form_info("info-test")
        finally:
            del app.FORMS["info-test"]
        self.assertEqual(info["author"], "Gordic spol. s r.o.")
        self.assertTrue(info["comment"].startswith("Formulář žádosti"))
        self.assertEqual(info["created"], "2016-07-04")
        self.assertEqual(info["modified"], "2016-07-04T09:20:44")
        for key in ("subject", "keywords", "code", "form_id"):
            self.assertIn(key, info)

    def test_saving_stamps_the_modification_time(self):
        source = ROOT / "samples/cuzk/form.zfo"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "saved.zfo"
            app.FORMS["save-test"] = source
            try:
                app.write_zfo("save-test", zipfile.ZipFile(source).read("form.fo"), target)
            finally:
                del app.FORMS["save-test"]
            with zipfile.ZipFile(target) as saved, zipfile.ZipFile(source) as original:
                stamp = datetime(*saved.getinfo("form.fo").date_time)
                self.assertLess(abs((datetime(*time.localtime()[:6]) - stamp).total_seconds()), 120)
                other = [n for n in original.namelist() if n != "form.fo"][0]
                self.assertEqual(saved.getinfo(other).date_time, original.getinfo(other).date_time)
                self.assertEqual(saved.read("form.fo"), original.read("form.fo"))


class PlainFoForms(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.zfo = ROOT / "samples/cuzk/form.zfo"
        self.path = self.folder / "form.fo"
        self.path.write_bytes(zipfile.ZipFile(self.zfo).read("form.fo"))
        for patcher in (mock.patch.dict(app.FORMS, {"plain": self.path}),
                        mock.patch.object(app, "SINGLE_FILE", True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def open_session(self, name="plain"):
        page = app.render_zfo_page(name)
        return app.SESSIONS[re.search(r"const SESSION = '([\w-]+)'", page).group(1)]

    def signing_identity(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Test")])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(datetime(2020, 1, 1))
                .not_valid_after(datetime(2040, 1, 1)).sign(key, hashes.SHA256()))
        return key, cert

    def test_publisher_signatures_verify_as_in_zfo(self):
        plain = app.zfo_signatures(self.path.read_bytes())
        zipped = app.zfo_signatures(zipfile.ZipFile(self.zfo).read("form.fo"))
        self.assertTrue(plain and all(signature["intact"] for signature in plain))
        self.assertEqual(plain, zipped)

    def test_description_has_the_file_time_and_no_summary(self):
        os.utime(self.path, (1_700_000_000, 1_700_000_000))
        info = app.form_info("plain")
        self.assertEqual(info["title"], "")
        self.assertEqual(info["modified"], datetime.fromtimestamp(1_700_000_000).isoformat())
        self.assertTrue(info["form_id"])

    def test_saving_keeps_plain_xml_in_the_same_file(self):
        result = app.save_zfo(self.open_session())
        self.assertEqual(result["saved"], str(self.path))
        self.assertTrue(self.path.read_bytes().startswith(b"<?xml"))
        self.assertTrue(all(signature["intact"] for signature in result["signatures"]))

    def test_filled_value_survives_saving_and_opening_again(self):
        self.path.write_bytes(zipfile.ZipFile(ROOT / "samples/dva-ucastnici/form.zfo").read("form.fo"))
        session = self.open_session()
        engine.handle(session["doc"], "store_val", {"field": "o1_prijmeni", "value": "Nováková", "input_id": "o1_prijmeni",
                                                    "repe": "table---opak1-rep-0"})
        app.save_zfo(session)
        reopened = engine.data_element(self.open_session()["doc"])
        self.assertEqual(reopened.xpath("//d:o1_prijmeni", namespaces=NS)[0].text, "Nováková")

    def test_signing_writes_a_plain_signed_copy_beside_the_form(self):
        session = self.open_session()
        with mock.patch.object(app.certstore, "load_identity", return_value=self.signing_identity()):
            result = app.save_zfo(session, "test")
        target = self.folder / "form-podepsany.fo"
        self.assertEqual(result["saved"], str(target))
        self.assertFalse(zipfile.is_zipfile(target))
        layers = zfo_verify.describe_signature_layers(zfo_verify.load_form_root(target))
        self.assertEqual(layers[0]["subject"], "Test")
        self.assertTrue(all(layer["intact"] for layer in layers))

    def test_saving_outside_a_single_file_window_keeps_the_extension(self):
        (self.folder / "out").mkdir()
        with mock.patch.object(app, "SINGLE_FILE", False), mock.patch.object(app, "ROOT", self.folder):
            result = app.save_zfo(self.open_session())
        self.assertEqual(result["saved"], str(self.folder / "out/plain-ulozeny.fo"))

    def test_the_content_decides_the_container_not_the_extension(self):
        zip_named_fo = self.folder / "zip.fo"
        zip_named_fo.write_bytes(self.zfo.read_bytes())
        xml_named_zfo = self.folder / "xml.zfo"
        xml_named_zfo.write_bytes(self.path.read_bytes())
        with mock.patch.dict(app.FORMS, {"zip-named-fo": zip_named_fo, "xml-named-zfo": xml_named_zfo}):
            app.save_zfo(self.open_session("zip-named-fo"))
            app.save_zfo(self.open_session("xml-named-zfo"))
        self.assertTrue(zipfile.is_zipfile(zip_named_fo))
        self.assertFalse(zipfile.is_zipfile(xml_named_zfo))



ISDS_SAMPLES = ROOT / "samples-isds"


def isds_sample_tool():
    if not hasattr(isds_sample_tool, "module"):
        spec = importlib.util.spec_from_file_location("make_isds_samples", ROOT / "tools/make-isds-samples.py")
        isds_sample_tool.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isds_sample_tool.module)
    return isds_sample_tool.module


def isds_sample(name):
    return (ISDS_SAMPLES / f"{name}.zfo").read_bytes()


class IsdsMessages(unittest.TestCase):
    def test_received_message_fields(self):
        message = isds_message.read_message(isds_sample("prijata"))
        info = message.info
        self.assertEqual((message.kind, info["kind"]), ("received", "received"))
        self.assertEqual(info["id"], "1234567")
        self.assertEqual(info["subject"], "Rozhodnutí ve věci TEST")
        self.assertEqual(info["sender"], {"name": "Úřad TEST", "address": "Testovací 1, 110 00 Praha 1", "box": "abc12de"})
        self.assertEqual(info["recipient"], {"name": "Firma TEST s.r.o.", "address": "Zkušební 2, 602 00 Brno", "box": "xyz34fg"})
        self.assertEqual((info["to_hands"], info["sender_ref"], info["sender_ident"], info["recipient_ref"], info["recipient_ident"]),
                         ("Jan Testovací", "MV-TEST-456/2026", "SZ-TEST-1", "TEST-123/2026", "SZ-TEST-9"))
        self.assertTrue(info["personal_delivery"])
        self.assertFalse(info["suspicious"])
        self.assertEqual((info["delivered"], info["accepted"], info["status"]),
                         ("2026-03-02T10:30:05.123+01:00", "2026-03-03T08:12:40.456+01:00", 6))
        self.assertEqual([(item["name"], item["mime"], item["main"]) for item in info["attachments"]],
                         [("rozhodnuti.pdf", "application/pdf", True), ("data.xml", "text/xml", False),
                          ("evil.txt", "text/plain", False)])
        self.assertEqual(message.attachments[0], b"%PDF-1.4\n% TEST priloha\n")
        self.assertEqual(etree.fromstring(message.attachments[1]).findtext("{urn:test}polozka"), "1")
        self.assertEqual([item["size"] for item in info["attachments"]], [len(content) for content in message.attachments])
        self.assertEqual(info["events"], [])

    def test_sent_message_and_receipt_kinds(self):
        self.assertEqual(isds_message.read_message(isds_sample("odeslana")).kind, "sent")
        receipt = isds_message.read_message(isds_sample("dorucenka"))
        self.assertEqual(receipt.kind, "receipt")
        self.assertEqual(receipt.info["attachments"], [])
        self.assertEqual(receipt.info["events"][1], {"time": "2026-03-02T10:30:05.000+01:00",
                                                     "description": "EV5: Zpráva byla dodána do schránky příjemce."})
        self.assertEqual(len(receipt.info["events"]), 3)

    def test_ber_indefinite_length_opens(self):
        self.assertEqual(isds_message.read_message(isds_sample("prijata-ber")).info,
                         isds_message.read_message(isds_sample("prijata")).info)

    def test_safe_file_names(self):
        self.assertEqual(isds_message.safe_file_names(["../../evil.txt", "C:\\x\\a.pdf", "CON.txt", ".bashrc", "a\u202egpj.exe",
                                                       "x\x00y", "", "A.pdf", "a.pdf"]),
                         ["evil.txt", "a.pdf", "_CON.txt", "bashrc", "agpj.exe", "xy", "priloha-7", "A (2).pdf", "a (3).pdf"])
        self.assertEqual(isds_message.safe_file_names(["C:x.txt", 'a<b>c"d|e?f*g.txt', "CON.tar.gz", "nul .txt", "a:b"]),
                         ["C_x.txt", "a_b_c_d_e_f_g.txt", "_CON.tar.gz", "_nul .txt", "a_b"])
        self.assertEqual(len(isds_message.safe_file_names(["a" * 300 + ".pdf"])[0]), 200)
        self.assertTrue(isds_message.safe_file_names(["a" * 300 + ".pdf"])[0].endswith(".pdf"))

    def test_not_a_message(self):
        tool = isds_sample_tool()
        doctype = b'<?xml version="1.0"?><!DOCTYPE q [<!ENTITY e "x">]><q:MessageDownloadResponse xmlns:q="http://isds.czechpoint.cz/v20/message"/>'
        for data in (zfo_verify.read_form_fo(ROOT / "samples/cuzk/form.zfo"), tool.build_message(xml=b"<x/>"),
                     tool.build_message(xml=doctype), b"\x30\x80"):
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                isds_message.read_message(data)

    def test_intact_seal_with_stamp(self):
        seal = isds_message.describe_seal(isds_message.read_message(isds_sample("prijata")))
        self.assertTrue(seal["intact"])
        self.assertTrue(seal["isds_operator"])
        self.assertEqual(seal["subject"], "ISDS TEST")
        self.assertEqual(seal["issuer"], "Blanketa TEST CA")
        self.assertEqual([(stamp["kind"], stamp["intact"], stamp["at"]) for stamp in seal["stamps"]],
                         [("signature", True, "2026-03-02T09:30:00+00:00")])
        self.assertEqual((seal["publisher"], seal["modified"], seal["changes_unchecked"], seal["covers_whole"]), (False, False, False, True))
        self.assertEqual(x509.load_der_x509_certificate(seal["der"]).subject.rfc4514_string().count("ISDS TEST"), 1)

    def test_tampered_content_breaks_the_seal(self):
        self.assertFalse(isds_message.describe_seal(isds_message.read_message(isds_sample("pozmenena")))["intact"])

    def test_foreign_signer_is_flagged(self):
        seal = isds_message.describe_seal(isds_message.read_message(isds_sample("cizi-pecet")))
        self.assertTrue(seal["intact"])
        self.assertFalse(seal["isds_operator"])
        self.assertEqual(seal["subject"], "Někdo jiný TEST")

    def test_earlier_operator_counts_as_isds(self):
        self.assertTrue(isds_message.describe_seal(isds_message.read_message(isds_sample("vyprsela")))["isds_operator"])

    def test_only_the_operators_own_seal_counts(self):
        tool = isds_sample_tool()

        def operator(*names):
            cert, _ = tool.issue([(x509.NameOID.COUNTRY_NAME, "CZ"), *names], tool.CA, tool.CA_KEY)
            return isds_message.is_isds_operator(tool.asn1_certificate(cert))

        organization = (x509.NameOID.ORGANIZATION_NAME, "Digitální a informační agentura")
        self.assertTrue(operator(organization, (x509.NameOID.ORGANIZATION_IDENTIFIER, "NTRCZ-17651921"),
                                 (x509.NameOID.COMMON_NAME, "ISDS")))
        self.assertTrue(operator((x509.NameOID.ORGANIZATION_IDENTIFIER, "NTRCZ-00007064"), (x509.NameOID.COMMON_NAME, "ISDS")))
        self.assertFalse(operator(organization, (x509.NameOID.ORGANIZATION_IDENTIFIER, "NTRCZ-17651921"),
                                  (x509.NameOID.GIVEN_NAME, "Jan"), (x509.NameOID.SURNAME, "Úředník"), (x509.NameOID.COMMON_NAME, "Jan Úředník")))
        self.assertFalse(operator((x509.NameOID.ORGANIZATION_IDENTIFIER, "NTRCZ-117651921"), (x509.NameOID.COMMON_NAME, "ISDS")))
        self.assertFalse(operator((x509.NameOID.SERIAL_NUMBER, "P100007064"), (x509.NameOID.COMMON_NAME, "ISDS")))
        self.assertFalse(operator((x509.NameOID.ORGANIZATION_NAME, "Firma IČO 00007064"), (x509.NameOID.COMMON_NAME, "ISDS")))

    def test_message_text_cannot_fill_page_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zprava.zfo"
            path.write_bytes(isds_sample_tool().build_message(subject="__SIGNATURES__ __SUBMITTED__ __SESSION__ __FILE__"))
            with mock.patch.dict(app.FORMS, {"placeholders": path}):
                page = app.render_message_page("placeholders")
        message = json.loads(page.split("const MESSAGE = ")[1].split(";\n")[0])
        self.assertEqual(message["subject"], "__SIGNATURES__ __SUBMITTED__ __SESSION__ __FILE__")
        self.assertEqual(json.loads(page.split("let signatures = ")[1].split(";\n")[0])[0]["subject"], "ISDS TEST")

    def test_attachment_text_stays_json(self):
        hostile = "</script><img src=x onerror=alert(1)>"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zprava.zfo"
            path.write_bytes(isds_sample_tool().build_message(attachments=((hostile + ".pdf", hostile, "main", b"%PDF-1.4\n"),)))
            with mock.patch.dict(app.FORMS, {"hostile": path}):
                page = app.render_message_page("hostile")
        self.assertNotIn("<img src=x", page)
        attachment = json.loads(page.split("const MESSAGE = ")[1].split(";\n")[0])["attachments"][0]
        self.assertEqual(attachment["mime"], hostile)
        self.assertTrue(attachment["main"])

    def test_receipt_has_no_stamp(self):
        self.assertEqual(isds_message.describe_seal(isds_message.read_message(isds_sample("dorucenka")))["stamps"], [])

    def test_submission_stamp_matches_hash(self):
        message = isds_message.read_message(isds_sample("prijata"))
        stamp = isds_message.submission_stamp(message)
        self.assertEqual((stamp["at"], stamp["issuer"], stamp["matches"]), ("2026-03-02T09:30:00+00:00", "Blanketa TEST TSA", True))
        message.xml_root.xpath("//*[local-name()='dmHash']")[0].text = base64.b64encode(b"x" * 32).decode()
        self.assertFalse(isds_message.submission_stamp(message)["matches"])

    def test_damaged_stamp_is_not_proof(self):
        info = cms.ContentInfo.load(isds_sample("prijata"))
        token = info["content"]["signer_infos"][0]["unsigned_attrs"][0]["values"][0]
        stamp_signer = token["content"]["signer_infos"][0]
        signature = bytearray(stamp_signer["signature"].native)
        signature[10] ^= 0xFF
        stamp_signer["signature"] = bytes(signature)
        seal = isds_message.describe_seal(isds_message.read_message(info.dump(force=True)))
        self.assertTrue(seal["intact"])
        self.assertFalse(seal["stamps"][0]["intact"])

    def test_unusual_status_is_left_out(self):
        for status in ("²", "٣", "x"):
            with self.subTest(status=status):
                message = isds_message.read_message(isds_sample_tool().build_message(status=status))
                self.assertIsNone(message.info["status"])

    def test_message_page_answers_form_menu_commands(self):
        with mock.patch.dict(app.FORMS, {"prijata": ISDS_SAMPLES / "prijata.zfo"}):
            page = app.render_message_page("prijata")
        fallback = re.search(r"window\.zfoAction = name => \(actions\[name\] \?\? (\w+)\)", page).group(1)
        self.assertIn("Datová zpráva je jen ke čtení", page.split(f"const {fallback} = ")[1].split("\n")[0])

    def test_message_page_keeps_czech_typography(self):
        page = (ROOT / "ui/message.html").read_text(encoding="utf-8")
        self.assertNotIn("razítka v pečetěném", page)
        self.assertNotRegex(page, r"\} (B|kB|MB)`")

    def test_message_files_are_recognised_by_their_first_byte(self):
        self.assertTrue(isds_message.looks_like_message(isds_sample("prijata-ber")))
        self.assertFalse(isds_message.looks_like_message(b"PK\x03\x04"))


# Copied from webview.util.parse_file_type: pywebview raises ValueError on anything else, and the tests run without it.
PYWEBVIEW_FILE_TYPE_PATTERN = r"^([\w ]+)\((\*(?:\.(?:\w+|\*))*(?:;\*(?:\.(?:\w+|\*))*)*)\)$"


class ContainerPages(unittest.TestCase):
    PDF = b"%PDF-1.7 smlouva"
    HOSTILE = "</script><img src=x onerror=alert(1)>.txt"

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        patcher = mock.patch.object(app, "SINGLE_FILE", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_container(self, data, name="smlouva.asice"):
        path = self.folder / name
        path.write_bytes(data)
        app.FORMS["container-test"] = path
        self.addCleanup(app.FORMS.pop, "container-test", None)
        return path

    def signed(self, files=None, **options):
        files = files or {"smlouva.pdf": self.PDF}
        signature = asic_samples.xades_signature(files, **options)
        return asic_samples.asic_e(files, {"signatures.xml": asic_samples.signatures_xml(signature)})

    def request(self, path, host=None, header="Content-Disposition"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("GET", path, headers={"Host": host or f"127.0.0.1:{self.port}"})
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.getheader(header), response.read()

    def page_json(self, page, marker):
        return json.loads(re.search(rf"const {marker} = (.*);\n", page).group(1))

    def post(self, path, body=b"[]"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("POST", path, body=body, headers={"Host": f"127.0.0.1:{self.port}"})
        response = connection.getresponse()
        return response.status, response.read()

    def test_container_session_answers_only_revocation_posts(self):
        _, session = self.render(self.signed())
        self.assertEqual(self.post(f"/revocation?s={session}"), (200, b"{}"))
        for path in ("/ipc", "/save", "/pdf-save", "/export-data"):
            with self.subTest(path=path):
                self.assertEqual(self.post(f"{path}?s={session}", b"{}")[0], 404)

    def render(self, data):
        self.open_container(data)
        page = app.render_container_page("container-test")
        return page, re.search(r"const SESSION = '([\w-]+)'", page).group(1)

    def test_container_page_carries_files_and_signatures_as_json(self):
        page, session = self.render(self.signed())
        container = self.page_json(page, "CONTAINER")
        self.assertEqual(self.page_json(page, "FILE"), "smlouva.asice")
        self.assertEqual([file["name"] for file in container["files"]], ["smlouva.pdf"])
        self.assertEqual(container["signatures"][0]["files"], ["smlouva.pdf"])
        self.assertNotIn("der", container["signatures"][0])
        self.assertEqual(app.SESSIONS[session]["entries"], [self.PDF])
        self.assertEqual(self.request("/?form=container-test")[0], 200)

    def test_container_file_route_needs_the_session(self):
        self.render(self.signed())
        self.assertEqual(self.request("/container-file?i=0")[0], 404)
        self.assertEqual(self.request("/container-file?s=nope&i=0")[0], 404)
        self.assertEqual(self.request("/container-file?s=x&i=0", host="attacker.example")[0], 403)

    def test_container_file_route_serves_by_index(self):
        _, session = self.render(self.signed())
        status, content_type, disposition, body = self.request(f"/container-file?s={session}&i=0")
        self.assertEqual((status, content_type, body), (200, "application/octet-stream", self.PDF))
        self.assertEqual(disposition, "attachment; filename*=UTF-8''smlouva.pdf")
        for index in ("99", "-1", "x", ""):
            self.assertEqual(self.request(f"/container-file?s={session}&i={index}")[0], 404, index)

    def test_no_entry_is_sniffed_as_a_page(self):
        _, session = self.render(self.signed({"smlouva.pdf": self.PDF, "stranka.html": b"<script>alert(1)</script>"}))
        for index in (0, 1):
            status, content_type, sniffing, _ = self.request(f"/container-file?s={session}&i={index}", header="X-Content-Type-Options")
            self.assertEqual((status, content_type, sniffing), (200, "application/octet-stream", "nosniff"), index)

    def test_shared_files_script_is_served_without_a_session(self):
        status, content_type, _, body = self.request("/files.js")
        self.assertEqual((status, content_type), (200, "text/javascript"))
        self.assertEqual(body, (ROOT / "ui/files.js").read_bytes())
        self.assertEqual(self.request("/files.js", host="attacker.example")[0], 403)

    def test_both_pages_use_the_shared_files_script(self):
        for page in ("container", "message"):
            self.assertIn('<script src="/files.js"></script>', (ROOT / f"ui/{page}.html").read_text(encoding="utf-8"), page)
        script = (ROOT / "ui/files.js").read_text(encoding="utf-8")
        self.assertNotRegex(script, r"\} (B|kB|MB|%)`")
        self.assertIn("Math.round(event.scale * 100) + '\u00a0%'", script)

    def test_non_pdf_entry_is_a_download_named_by_its_basename(self):
        files = {"složka/příloha č. 1.txt": b"text"}
        _, session = self.render(self.signed(files))
        status, content_type, disposition, body = self.request(f"/container-file?s={session}&i=0")
        self.assertEqual((status, content_type, body), (200, "application/octet-stream", b"text"))
        self.assertEqual(disposition, "attachment; filename*=UTF-8''p%C5%99%C3%ADloha%20%C4%8D.%201.txt")

    def test_hostile_file_name_stays_json(self):
        page, _ = self.render(self.signed({self.HOSTILE: b"x"}))
        self.assertNotIn("</script><img", page)
        self.assertNotIn("<img src=x", page)
        self.assertEqual(self.page_json(page, "CONTAINER")["files"][0]["name"], self.HOSTILE)

    def test_markers_in_file_data_are_not_substituted(self):
        page, _ = self.render(self.signed({"__FILE__.txt": b"x", "__SESSION__.txt": b"y"}))
        self.assertEqual([file["name"] for file in self.page_json(page, "CONTAINER")["files"]], ["__FILE__.txt", "__SESSION__.txt"])
        self.open_container(self.signed(), "__CONTAINER__.asice")
        page = app.render_container_page("container-test")
        self.assertEqual(self.page_json(page, "FILE"), "__CONTAINER__.asice")
        self.assertEqual(self.page_json(page, "CONTAINER")["files"][0]["name"], "smlouva.pdf")

    def test_huge_index_is_not_found(self):
        _, session = self.render(self.signed())
        self.assertEqual(self.request(f"/container-file?s={session}&i={'9' * 5000}")[0], 404)

    def test_container_without_pdf_renders(self):
        page, _ = self.render(self.signed({"a.txt": b"a", "b.txt": b"b"}))
        self.assertEqual(len(self.page_json(page, "CONTAINER")["files"]), 2)

    def test_untrusted_intact_signature_is_not_broken(self):
        page, _ = self.render(self.signed())
        signature = self.page_json(page, "CONTAINER")["signatures"][0]
        self.assertIs(signature["intact"], True)
        self.assertIs(signature["trusted"], False)

    def test_signature_file_without_certificate_still_renders(self):
        data = asic_samples.asic_e({"smlouva.pdf": self.PDF}, {"signatures.xml": b"<broken"})
        page, _ = self.render(data)
        signature = self.page_json(page, "CONTAINER")["signatures"][0]
        self.assertEqual((signature["intact"], signature["trusted"], signature["unverifiable"], signature["certificate"]),
                         (False, False, False, ""))
        self.assertNotIn("der", signature)

    def test_damaged_container_shows_the_damaged_page(self):
        data = self.signed()
        self.open_container(data[:len(data) // 2])
        status, _, _, body = self.request("/?form=container-test")
        self.assertEqual(status, 422)
        self.assertIn("Soubor nelze otevřít".encode(), body)

    def test_zfo_is_not_taken_for_a_container(self):
        self.open_container((ROOT / "samples/cuzk/form.zfo").read_bytes(), "form.zfo")
        self.assertEqual(self.request("/?form=container-test")[0], 200)
        self.assertNotIn("const CONTAINER", self.request("/?form=container-test")[3].decode())


class FileAssociations(unittest.TestCase):
    FO_MIME_TYPE = "application/vnd.software602.filler.form+xml"

    def test_macos_declares_the_type_and_the_document(self):
        with open(ROOT / "macapp/Info.plist", "rb") as info:
            plist = plistlib.load(info)
        declared = next(item for item in plist["UTImportedTypeDeclarations"]
                        if item["UTTypeTagSpecification"]["public.filename-extension"] == ["fo"])
        document_types = [content_type for item in plist["CFBundleDocumentTypes"] for content_type in item["LSItemContentTypes"]]
        self.assertIn(declared["UTTypeIdentifier"], document_types)

    def test_windows_registers_the_extension(self):
        script = (ROOT / "packaging/windows/blanketa.iss").read_text(encoding="utf-8")
        self.assertIn(r'Software\Classes\.fo\OpenWithProgids"; ValueType: string; ValueName: "Blanketa.fo"', script)
        self.assertIn(r'Software\Classes\Blanketa.fo\shell\open\command', script)

    def test_linux_packages_declare_the_type(self):
        for name in ("blanketa.desktop", "install.sh"):
            self.assertIn(self.FO_MIME_TYPE, (ROOT / "packaging/linux" / name).read_text(encoding="utf-8"), name)
        for name in ("blanketa-mime.xml", "install.sh"):
            self.assertIn('<glob pattern="*.fo" weight="60"/>', (ROOT / "packaging/linux" / name).read_text(encoding="utf-8"), name)

    ASIC_TYPES = {"application/vnd.etsi.asic-e+zip": ("asice", "sce"), "application/vnd.etsi.asic-s+zip": ("asics", "scs")}

    def test_asic_containers_are_registered_on_every_platform(self):
        with open(ROOT / "macapp/Info.plist", "rb") as info:
            plist = plistlib.load(info)
        document_types = [content_type for item in plist["CFBundleDocumentTypes"] for content_type in item["LSItemContentTypes"]]
        for mime, extensions in self.ASIC_TYPES.items():
            declared = next(item for item in plist["UTImportedTypeDeclarations"]
                            if item["UTTypeTagSpecification"]["public.mime-type"] == mime)
            self.assertEqual(declared["UTTypeTagSpecification"]["public.filename-extension"], list(extensions))
            self.assertIn("public.zip-archive", declared["UTTypeConformsTo"])
            self.assertIn(declared["UTTypeIdentifier"], document_types)
        script = (ROOT / "packaging/windows/blanketa.iss").read_text(encoding="utf-8")
        self.assertIn(r'Software\Classes\Blanketa.asic\shell\open\command', script)
        for extension in ("asice", "sce", "asics", "scs"):
            self.assertIn(rf'Software\Classes\.{extension}\OpenWithProgids"; ValueType: string; ValueName: "Blanketa.asic"', script)
        for extension in ("asice", "asics"):
            self.assertIn(rf'Software\Classes\.{extension}"; ValueType: string; ValueName: ""; ValueData: "Blanketa.asic"; Tasks: asicdefault', script)
        for name in ("blanketa.desktop", "install.sh"):
            text = (ROOT / "packaging/linux" / name).read_text(encoding="utf-8")
            for mime in self.ASIC_TYPES:
                self.assertIn(mime + ";", text, name)
        for name in ("blanketa-mime.xml", "install.sh"):
            text = (ROOT / "packaging/linux" / name).read_text(encoding="utf-8")
            for mime, extensions in self.ASIC_TYPES.items():
                self.assertIn(f'<mime-type type="{mime}">', text, name)
                for extension in extensions:
                    self.assertIn(f'<glob pattern="*.{extension}"/>', text, name)

    def test_zfo_type_names_data_messages_too(self):
        description = "Formulář 602XML nebo datová zpráva (ZFO)"
        for path in ("macapp/Info.plist", "packaging/windows/blanketa.iss", "packaging/linux/blanketa-mime.xml",
                     "packaging/linux/install.sh"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn(description, text, path)
            self.assertNotIn("Formulář 602XML (ZFO)", text, path)
        for path in ("packaging/linux/blanketa.desktop", "packaging/linux/install.sh", "packaging/linux/control"):
            self.assertIn("datových zpráv", (ROOT / path).read_text(encoding="utf-8"), path)

    def test_file_dialogs_offer_the_extension(self):
        self.assertTrue(any("*.fo" in file_type for file_type in desktop.OPEN_DIALOG_FILE_TYPES))
        for file_type in desktop.OPEN_DIALOG_FILE_TYPES:
            self.assertRegex(file_type, PYWEBVIEW_FILE_TYPE_PATTERN)
        self.assertIn('UTType(filenameExtension: "fo")', (ROOT / "macapp/main.swift").read_text(encoding="utf-8"))


class UnsupportedForms(unittest.TestCase):
    @staticmethod
    def parsed(xml):
        return etree.fromstring(f'<f xmlns:fm="http://software602.cz/forms">{xml}</f>')

    def test_notice_stays_outside_the_zoomed_page(self):
        # Whatever is not listed as chrome moves into .zfo-page and would scale with the page zoom.
        chrome = re.search(r"const chrome = \[\.\.\.document\.querySelectorAll\('([^']+)'\)", app.SHELL).group(1)
        self.assertIn(".zfo-notice", chrome)

    def test_form_with_scripts_that_run_is_not_reported(self):
        for sample in ("cssz-onz", "olk-dotace", "cuzk"):
            doc = etree.fromstring(zipfile.ZipFile(ROOT / f"samples/{sample}/form.zfo").read("form.fo"), etree.XMLParser(huge_tree=True))
            self.assertEqual(app.binds_with_unsupported_scripts(doc), [])

    def test_braces_alone_are_not_javascript(self):
        doc = self.parsed('<fm:bind Id="x" calculate="{../d:a}"/><fm:bind Id="y" calculate="  { var a = SendData(); }"/>'
                          '<fm:bind Id="z" constraint="{GetDocument().SendData()}"/><fm:bind Id="w" calculate="{ var a = 1; }"/>')
        self.assertEqual(app.binds_with_unsupported_scripts(doc), ["y", "z"])

    def test_script_actions_are_counted_and_xpath_ones_are_not(self):
        doc = self.parsed('<a form602attchuploadstartaction="{ SendData(); }"/><a form602attchuploadstartaction=" { x(); }"/>'
                          '<a form602attchuploadstartaction="SendAppEvent(1)"/><a form602btnclick="{ SendData(); }"/>')
        self.assertEqual(app.count_never_run_scripts(doc), 2)

    @staticmethod
    def pdf_with(xfa, source):
        writer = PdfWriter(clone_from=source)
        writer.root_object[NameObject("/AcroForm")][NameObject("/XFA")] = TextStringObject("<xdp/>")
        if xfa == "dynamic":
            writer.root_object[NameObject("/AcroForm")][NameObject("/Fields")] = ArrayObject()
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    def test_xfa_is_recognised(self):
        source = ROOT / "samples-pdf/zadost.pdf"
        self.assertEqual(app.pdf_info(source.read_bytes())["xfa"], "")
        self.assertEqual(app.pdf_info(self.pdf_with("hybrid", source))["xfa"], "hybrid")
        self.assertEqual(app.pdf_info(self.pdf_with("dynamic", source))["xfa"], "dynamic")

    def test_pdf_without_acroform_has_no_xfa(self):
        writer, out = PdfWriter(), io.BytesIO()
        writer.add_blank_page(100, 100)
        writer.write(out)
        self.assertEqual(app.pdf_info(out.getvalue())["xfa"], "")


class SignatureFieldNames(unittest.TestCase):
    """Czech offices name signature fields like "El.podpis [0]", which pyHanko's analysis of later changes refuses."""

    KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    NAME = x509.Name([x509.NameAttribute(x509.NameOID.COUNTRY_NAME, "CZ"), x509.NameAttribute(x509.NameOID.COMMON_NAME, "Test")])
    CERT = (x509.CertificateBuilder().subject_name(NAME).issuer_name(NAME).public_key(KEY.public_key()).serial_number(1)
            .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1)).not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
            .sign(KEY, hashes.SHA256()))

    def signed(self):
        writer, out = PdfWriter(), io.BytesIO()
        writer.add_blank_page(200, 200)
        field = writer._add_object(DictionaryObject({
            NameObject("/FT"): NameObject("/Sig"), NameObject("/T"): TextStringObject("El.podpis [0]"),
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Rect"): ArrayObject([NumberObject(0)] * 4), NameObject("/F"): NumberObject(132)}))
        writer.pages[0][NameObject("/Annots")] = ArrayObject([field])
        writer.root_object[NameObject("/AcroForm")] = writer._add_object(DictionaryObject({NameObject("/Fields"): ArrayObject([field])}))
        writer.write(out)
        return pdf_sign.sign_pdf(out.getvalue(), self.KEY, self.CERT)

    def test_last_signature_covering_the_file_is_intact(self):
        found = pdf_sign.describe_signatures(self.signed())[0]
        self.assertTrue(found["intact"])
        self.assertTrue(found["covers_whole"])
        self.assertFalse(found["changes_unchecked"])

    def test_later_changes_that_cannot_be_analysed_are_reported(self):
        first, second = pdf_sign.describe_signatures(pdf_sign.sign_pdf(self.signed(), self.KEY, self.CERT))
        self.assertTrue(first["intact"])
        self.assertFalse(first["covers_whole"])
        self.assertTrue(first["changes_unchecked"])
        self.assertFalse(first["modified"])
        self.assertFalse(second["changes_unchecked"])

    def test_changed_content_is_still_broken(self):
        signed = self.signed()
        at = signed.index(b"/MediaBox")
        found = pdf_sign.describe_signatures(signed[:at] + b"/MediaBoy" + signed[at + 9:])[0]
        self.assertFalse(found["intact"])
        self.assertFalse(found["changes_unchecked"])

    def test_ordinary_field_is_analysed(self):
        document = pdf_sign.sign_pdf(pdf_sign.sign_pdf((ROOT / "samples-pdf/zadost.pdf").read_bytes(), self.KEY, self.CERT), self.KEY, self.CERT)
        self.assertFalse(any(found["changes_unchecked"] for found in pdf_sign.describe_signatures(document)))


class ChoiceFieldsAfterSigning(unittest.TestCase):
    """A choice field keeps its selected indices (/I) only where ISO 32000-1 lets /V outrank them."""

    def field(self, name):
        return PdfReader(io.BytesIO(self.filled)).get_fields()[name]

    def test_single_select_keeps_earlier_signature_clean(self):
        signed = pdf_sign.sign_pdf((ROOT / "samples-pdf/zadost.pdf").read_bytes(), SignatureFieldNames.KEY, SignatureFieldNames.CERT)
        self.filled = pdf_sign.fill_form_fields(signed, {"ucel": "Pro soud"})
        self.assertFalse(pdf_sign.describe_signatures(self.filled)[0]["modified"])
        self.assertEqual(self.field("ucel")["/V"], "Pro soud")

    def test_multi_select_indices_follow_the_new_value(self):
        writer, out = PdfWriter(), io.BytesIO()
        writer.add_blank_page(200, 200)
        field = writer._add_object(DictionaryObject({
            NameObject("/FT"): NameObject("/Ch"), NameObject("/T"): TextStringObject("volby"),
            NameObject("/Ff"): NumberObject(pdf_sign.MULTI_SELECT), NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"), NameObject("/Rect"): ArrayObject([NumberObject(0)] * 4),
            NameObject("/Opt"): ArrayObject(TextStringObject(option) for option in ("a", "b", "c")),
            NameObject("/V"): ArrayObject([TextStringObject("a")]), NameObject("/I"): ArrayObject([NumberObject(0)])}))
        writer.pages[0][NameObject("/Annots")] = ArrayObject([field])
        writer.root_object[NameObject("/AcroForm")] = writer._add_object(DictionaryObject({NameObject("/Fields"): ArrayObject([field])}))
        writer.write(out)
        self.filled = pdf_sign.fill_form_fields(out.getvalue(), {"volby": ["b", "c"]})
        self.assertEqual(self.field("volby").indirect_reference.get_object()["/I"], [1, 2])


class OpeningForms(unittest.TestCase):
    """Most of the time goes in the page, which no test here can run; these guard the engine's part and the page's fixes."""
    SECTIONS = 50

    @classmethod
    def setUpClass(cls):
        with zipfile.ZipFile(ROOT / "samples/dva-ucastnici/form.zfo") as source:
            parts = [(item, source.read(item.filename)) for item in source.infolist()]
        doc = etree.fromstring(next(data for item, data in parts if item.filename == "form.fo"), etree.XMLParser(huge_tree=True))
        sections = doc.xpath("//dsig:Object[not(@Id)]//*[local-name()='opak1']", namespaces={"dsig": app.DS})
        for _ in range(cls.SECTIONS - len(sections)):
            sections[-1].addnext(copy.deepcopy(sections[0]))
        cls.folder = tempfile.TemporaryDirectory()
        path = Path(cls.folder.name) / "form.zfo"
        with zipfile.ZipFile(path, "w") as out:
            for item, data in parts:
                out.writestr(item, etree.tostring(doc) if item.filename == "form.fo" else data)
        app.FORMS["many-sections"] = path

    @classmethod
    def tearDownClass(cls):
        del app.FORMS["many-sections"]
        cls.folder.cleanup()

    def test_engine_answers_quickly_with_many_sections(self):
        started = time.monotonic()
        page = app.render_zfo_page("many-sections")
        session = app.SESSIONS[re.search(r"const SESSION = '([\w-]+)'", page).group(1)]
        for message in ("document.ready", "init_rep_sections", "get_readonly_binds", "get_selected_listboxes",
                        "get_multiswitch_list", "get_repeatable"):
            answer = app.engine.handle(session["doc"], message, "")
        # The engine's share only, usually about two seconds; generous for a loaded CI runner.
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(answer[0]["data"].count('"o1_porad_cislo"'), self.SECTIONS)

    def test_form_is_not_painted_before_the_shell_styles_it(self):
        page = app.render_zfo_page("many-sections")
        self.assertLess(page.index(app.OPENING_PLACEHOLDER_STYLE), page.index("<body"))
        self.assertIn("body:not(.zfo-app) > * { display: none; }", app.OPENING_PLACEHOLDER_STYLE)

    def test_page_keeps_its_opening_fixes(self):
        # Removing one breaks nothing else, the form just opens slowly again.
        for fix in ("window.get_html_elem =", "window.fill_inputs =", "window.set_multiswitch_controls_callback =",
                    "window.populate_repeating_sections =", "REFRESHES.has(type)", "classList.add('zfo-opening')",
                    "AVAILABLE_WHILE_OPENING.has(name)", "window.send_rep_sections_originals =",
                    "window.set_readonly_inputs_callback =", "window.field_changed_error_callback ="):
            self.assertIn(fix, app.SHELL)

    def test_page_lays_out_only_after_opening(self):
        self.assertNotIn("]:visible", app.SHELL)  # jQuery measures it, and a page still hidden has no visible field


class ControlsWithoutOwnName(unittest.TestCase):
    """Some forms name a radio button or check box only by its bind; an empty name stopped the page loading its values (#46)."""
    FORM = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:chb>1</d:chb><d:rad>b</d:rad><d:old>1</d:old></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="chb" nodeset="d:chb"/><fm:bind Id="rad" nodeset="d:rad"/><fm:bind Id="old" nodeset="d:old"/></fm:bind>
</fm:model></fm:formparams>
<fo:page-sequence master-reference="p"><fo:flow flow-name="xsl-region-body"><fo:block>
<fo:character form602bind="chb" form602ct="chb" form602on="1" form602off="0" character="x"/>
<fo:character form602bind="rad" form602ct="rad" form602on="a" character="x"/>
<fo:character form602bind="rad" form602ct="rad" form602on="b" character="x"/>
<fo:character form602chkbx="old" form602bind="old" form602ct="chb" form602on="1" form602off="0" character="x"/>
<fo:character form602ct="rad" form602id="nothing" form602on="c" character="x"/>
</fo:block></fo:flow></fo:page-sequence></fo:root></dsig:Object></dsig:Signature>"""

    def inputs(self, kind):
        page = etree.tostring(app.XSLT(etree.ElementTree(etree.fromstring(self.FORM)), PATH_TO_JS="'/s/'", PATH_TO_CSS="'/s/'"),
                              method="html", encoding="unicode")
        return lxml_html.fromstring(page).xpath(f"//input[@type='{kind}']")

    def test_bind_names_the_control(self):
        boxes = self.inputs("checkbox")
        self.assertEqual([(box.get("id"), box.get("name")) for box in boxes], [("chb", "chb"), ("old", "old")])
        self.assertEqual([(radio.get("id"), radio.get("name")) for radio in self.inputs("radio")[:2]], [("rad-a", "rad"), ("rad-b", "rad")])

    def test_page_gets_values_of_controls_named_by_bind(self):
        doc = engine.open_document(self.FORM.encode())
        answer = engine.handle(doc, "get_init_values", {"chb": {"type": "check", "bind": "chb"}, "rad": {"type": "radio", "bind": "rad"}})
        values = json.loads(answer[0]["data"][len("set_inputs_init_values_callback("):-1])
        self.assertEqual((values["chb"]["value"], values["rad"]["value"]), ("on", "b"))

    def test_control_without_any_name_does_not_reach_the_page(self):
        # The page builds the selector "#" from an empty key, and that stops it before it reaches the other fields.
        doc = engine.open_document(self.FORM.encode())
        self.assertEqual(self.inputs("radio")[2].get("name"), "")
        answer = engine.handle(doc, "get_init_values", {"": {"type": "radio", "bind": ""}, "chb": {"type": "check", "bind": "chb"}})
        self.assertEqual(list(json.loads(answer[0]["data"][len("set_inputs_init_values_callback("):-1])), ["chb"])


class TimeStamps(unittest.TestCase):
    """samples-pdf/razitka comes from tools/make-timestamp-samples.py."""
    FOLDER = ROOT / "samples-pdf/razitka"

    def read(self, name):
        return pdf_sign.describe_signatures((self.FOLDER / f"{name}.pdf").read_bytes())

    @staticmethod
    def stamp(kind="signature", at="2026-01-15T10:00:00+00:00", intact=True):
        return {"kind": kind, "intact": intact, "at": at if intact else None, "issuer": "TSA", "der": b"certificate"}

    def judge(self, stamps, verdict):
        sig = {"stamps": stamps}
        with mock.patch.object(app.certstore, "stamp_trust_verdict", return_value=verdict) as asked:
            app.judge_time_stamps(sig)
        return sig, asked

    def test_signature_without_a_stamp_has_none(self):
        self.assertEqual(self.read("bez-razitka")[0]["stamps"], [])

    def test_stamp_inside_a_signature_gives_its_time_and_authority(self):
        found = self.read("razitko-v-podpisu")[0]
        self.assertTrue(found["intact"])
        self.assertEqual(found["stamps"][0]["kind"], "signature")
        self.assertEqual(found["stamps"][0]["at"], "2026-01-15T10:00:00+00:00")
        self.assertEqual(found["stamps"][0]["issuer"], "Blanketa TEST TSA")

    def test_document_stamp_is_not_a_broken_signature(self):
        signature, stamp = self.read("razitko-dokumentu")
        self.assertTrue(stamp["document_stamp"])
        self.assertTrue(stamp["intact"])
        self.assertTrue(signature["intact"])

    def test_document_stamp_proves_a_signature_it_covers(self):
        signature = self.read("razitko-dokumentu")[0]
        self.assertEqual([(s["kind"], s["at"]) for s in signature["stamps"]], [("document", "2026-01-16T10:00:00+00:00")])

    def test_document_stamp_before_the_signature_proves_nothing(self):
        stamp, signature = self.read("razitko-pred-podpisem")
        self.assertTrue(stamp["document_stamp"])
        self.assertEqual(signature["stamps"], [])

    def test_document_stamp_judged_for_its_card_and_its_signature(self):
        data = (self.FOLDER / "razitko-dokumentu.pdf").read_bytes()
        trust = [{"trusted": False, "unverifiable": False, "source": "system"}] * 2
        with (mock.patch.object(app.certstore, "stamp_trust_verdict", return_value={"qualified": True, "unverifiable": False, "reason": ""}),
              mock.patch.object(app.certstore, "trust_verdicts", return_value=trust)):
            signature, stamp = app.pdf_signatures(data)
        self.assertEqual(signature["proof"]["kind"], "document")
        self.assertEqual(stamp["proof"]["kind"], "document")

    def test_document_stamp_moved_behind_a_signature_proves_nothing(self):
        found = {bool(sig.get("document_stamp")): sig for sig in self.read("razitko-premistene")}
        self.assertTrue(found[True]["intact"])
        self.assertEqual(found[False]["stamps"], [])

    def test_damaged_stamp_attribute_keeps_the_panel_working(self):
        data = (self.FOLDER / "razitko-v-podpisu.pdf").read_bytes()
        at = data.lower().index(b"060b2a864886f70d010910020e")  # the attribute's type; a signature does not cover it
        found = pdf_sign.describe_signatures(data[:at] + b"05" + data[at + 2:])[0]
        self.assertFalse(found["stamps"][0]["intact"])

    def test_stamp_of_another_document_does_not_break_the_signature(self):
        found = self.read("razitko-jineho-dokumentu")[0]
        self.assertTrue(found["intact"])
        self.assertFalse(found["stamps"][0]["intact"])

    def test_changed_content_breaks_a_document_stamp(self):
        data = (self.FOLDER / "razitko-dokumentu.pdf").read_bytes()
        at = data.index(b"/MediaBox")
        stamp = pdf_sign.describe_signatures(data[:at] + b"/MediaBoy" + data[at + 9:])[1]
        self.assertFalse(stamp["intact"])

    def test_qualified_stamp_is_proof(self):
        sig, asked = self.judge([self.stamp()], {"qualified": True, "unverifiable": False, "reason": ""})
        self.assertEqual(sig["proof"]["at"], "2026-01-15T10:00:00+00:00")
        self.assertEqual(sig["stamp_problem"], "")
        self.assertEqual(asked.call_args.args[1], datetime(2026, 1, 15, 10, tzinfo=timezone.utc))
        self.assertNotIn("der", sig["proof"])

    def test_earliest_good_stamp_counts(self):
        later = self.stamp("document", "2030-01-01T00:00:00+00:00")
        sig, _ = self.judge([later, self.stamp()], {"qualified": True, "unverifiable": False, "reason": ""})
        self.assertEqual(sig["proof"]["kind"], "signature")

    def test_untrusted_stamp_is_no_proof_and_says_why(self):
        sig, _ = self.judge([self.stamp()], {"qualified": False, "unverifiable": False, "reason": "Není kvalifikované."})
        self.assertIsNone(sig["proof"])
        self.assertEqual(sig["stamp_problem"], "Není kvalifikované.")

    def test_damaged_stamp_is_no_proof_and_asks_nothing(self):
        sig, asked = self.judge([self.stamp(intact=False)], {"qualified": True, "unverifiable": False, "reason": ""})
        self.assertIsNone(sig["proof"])
        self.assertIn("poškozené", sig["stamp_problem"])
        asked.assert_not_called()

    def test_unverifiable_stamp_is_not_called_untrusted(self):
        sig, _ = self.judge([self.stamp()], {"qualified": False, "unverifiable": True, "reason": "Seznam nejde načíst."})
        self.assertIsNone(sig["proof"])
        self.assertTrue(sig["stamp_unverifiable"])

    def test_good_stamp_hides_a_bad_one(self):
        sig, _ = self.judge([self.stamp(intact=False), self.stamp()], {"qualified": True, "unverifiable": False, "reason": ""})
        self.assertIsNotNone(sig["proof"])
        self.assertEqual(sig["stamp_problem"], "")


@unittest.skipUnless(shutil.which("node"), "needs Node.js; CI runners have it")
class SignatureReplacementNotice(unittest.TestCase):
    """Runs the button answers of ui/shell.html: a change of the data shows that the data signature will be replaced."""
    SCRIPT = """
    const window = globalThis;
    let changed = false, ready = true, notices = 0;
    const renderSignatures = () => notices++;
    const queue = Promise.resolve(), document = {getElementsByName: () => []}, calls = [];
    for (const name of ['gtk_handler_start', 'gtk_handler_finished', 'send', 'set_inputs_init_values', 'redrawSections',
                        'calc_repeatable_sections', 'set_readonly_inputs', 'set_multiswitch_controls', 'setStatus', 'alert',
                        'showCheck', 'focusField']) window[name] = () => calls.push(name);
    const plainTextOf = text => text;
    %s
    eval(process.argv[1]);
    console.log(JSON.stringify({notices, calls}));
    """
    ANSWER = {"values": {}, "changed": False, "messages": [], "notices": [], "focus": None, "problems": []}

    def page_after(self, call):
        page = app.SHELL
        parts = [re.search(pattern, page, re.S).group(0) for pattern in (
            r"const noteChange = .*?\n  \};", r"window\.perform_click_action_callback = .*?\n  \};",
            r"window\.form_script_callback = .*?\n  \};", r"window\.data_changed_callback = .*?;\n")]
        script = self.SCRIPT % "\n".join(parts)
        return json.loads(subprocess.run(["node", "-e", script, "--", call], capture_output=True, text=True, check=True).stdout)

    def notices_after(self, call):
        return self.page_after(call)["notices"]

    def test_xpath_button_that_changed_the_data(self):
        self.assertEqual(self.notices_after('perform_click_action_callback({changed: true})'), 1)
        self.assertEqual(self.notices_after('perform_click_action_callback({changed: false})'), 0)

    def test_script_button_that_changed_the_data(self):
        changed = {**self.ANSWER, "changed": True}
        self.assertEqual(self.notices_after(f"form_script_callback({json.dumps(changed)})"), 1)
        self.assertEqual(self.notices_after(f"form_script_callback({json.dumps(self.ANSWER)})"), 0)

    def test_notice_is_drawn_once(self):
        self.assertEqual(self.notices_after('perform_click_action_callback({changed: true}); perform_click_action_callback({changed: true})'), 1)

    def test_engine_answer_that_the_data_changed(self):
        self.assertEqual(self.notices_after('data_changed_callback()'), 1)

    def test_script_answer_refreshes_every_copy_of_the_sections(self):
        # result.values names a field only by its bind; the values of the second and further copies come with the refresh.
        self.assertIn("calc_repeatable_sections", self.page_after(f"form_script_callback({json.dumps(self.ANSWER)})")["calls"])


class DataChangesOutsideFields(unittest.TestCase):
    """Repeating sections, attachments and scripts change the data without a stored value; the page has to learn of it."""
    FORM = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:priloha/><d:jmeno/>
<d:sekce><d:b/><d:c>ne</d:c></d:sekce><d:sekce><d:b/><d:c>ne</d:c></d:sekce></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="priloha" nodeset="d:priloha"/><fm:bind Id="jmeno" nodeset="d:jmeno"/>
<fm:bind Id="sekce" nodeset="d:sekce"><fm:bind Id="b" nodeset="d:b"/><fm:bind Id="c" nodeset="d:c"/></fm:bind></fm:bind>
</fm:model></fm:formparams>
<fo:page-sequence master-reference="p"><fo:flow flow-name="xsl-region-body"><fo:block>
<fo:inline form602ct="bin" form602bind="priloha" filenamebind="jmeno"/>
<fo:block form602ct="rep" form602bind="sekce" form602repe="sekce"><fo:inline form602ct="txt" form602bind="b"/>
<fo:character form602bind="c" form602ct="chb" form602on="ano" form602off="ne" character="x"/>
<fo:inline form602ct="btn" form602btnclick="{ var e = GetDocument().GetElement(); e.SetDataTo('b', 'druha'); e.SetDataTo('c', 'ano'); }">Zapsat</fo:inline>
</fo:block></fo:block></fo:flow></fo:page-sequence></fo:root></dsig:Object></dsig:Signature>"""
    SECOND_COPY = "block---sekce-rep-1"

    def setUp(self):
        self.doc = engine.open_document(self.FORM.encode())
        engine.handle(self.doc, "document.ready", "")

    def answers(self, message, data):
        return [answer["data"] for answer in engine.handle(self.doc, message, data) if answer["type"] == "javascript"]

    def sections_on_page(self):
        answer = self.answers("get_repeatable", "")[0]
        return json.loads(answer[len("calc_repeatable_sections_callback("):-1])["sekce"]

    def test_adding_and_removing_a_copy_of_a_section_changes_the_data(self):
        self.assertIn("data_changed_callback()", self.answers("add_section", {"name": "sekce-rep-0", "repe": "", "rs_id": "s"}))
        self.assertIn("data_changed_callback()", self.answers("del_section", {"name": "sekce-rep-2", "repe": "block---sekce-rep-2"}))
        self.assertEqual(len(self.sections_on_page()), 2)

    def test_deleting_an_attachment_changes_the_data(self):
        with tempfile.TemporaryDirectory() as folder:
            attachment = Path(folder, "doklad.txt")
            attachment.write_text("obsah")
            self.doc.attach_file(str(attachment), "priloha", None)
        deletion = {"field": "priloha", "value": "doklad.txt", "repe": "", "input_id": "p"}
        self.assertIn("data_changed_callback()", self.answers("delete_attachment", deletion))
        self.assertNotIn("data_changed_callback()", self.answers("delete_attachment", deletion))

    def test_script_value_in_a_further_copy_reaches_that_copy_on_the_page(self):
        engine.handle(self.doc, "run_form_script", {"button": 0, "repe": self.SECOND_COPY})
        self.assertEqual(self.sections_on_page(), [{"b": "", "c": "off"}, {"b": "druha", "c": "on"}])


@unittest.skipUnless(shutil.which("node"), "needs Node.js; CI runners have it")
class RadioButtonsInSectionCopies(unittest.TestCase):
    """Runs the radio button handling of ui/shell.html on a page with a group outside sections and two copies of a section."""
    SCRIPT = """
    const window = globalThis, sent = [];
    const formsById = new Map();
    const document = {body: {append() {}}, createElement: tag => tag === 'div'
      ? {children: [], append(form) { this.children.push(form); formsById.set(form.id, form); }, get childElementCount() { return this.children.length; }}
      : {id: ''}};
    const copies = [{name: 'sekce-rep-0'}, {name: 'sekce-rep-1'}];
    const radio = (name, value, copy) => ({type: 'radio', name, value, checked: false, copy, id: `${name}-${value}`, attributes: {},
      closest: () => copy, getAttribute(key) { return this.attributes[key] ?? null; }, setAttribute(key, text) { this.attributes[key] = text; },
      get form() { return formsById.get(this.attributes.form) ?? null; }});
    const radios = [radio('mimo', 'x', null), radio('mimo', 'y', null), ...copies.flatMap(copy => ['a', 'b'].map(value => radio('r', value, copy)))];
    const page = {querySelectorAll: () => radios};
    const elementsNamed = name => radios.filter(candidate => candidate.name === name);
    const $ = element => element?.wrapped ? element : {wrapped: element, hasClass: () => false};
    const is_array = Array.isArray;
    const get_html_elem = (parent, name) => copies.find(copy => copy.name === name)
      ?? radios.find(candidate => candidate.name === name && candidate.copy === (parent?.wrapped ?? null));
    const set_input_value = () => { throw new Error('a radio button reached set_input_value'); };
    const gtk_handler_start = () => {}, send = message => sent.push(JSON.parse(message)), setStatus = () => {};
    const shortOneLine = text => text, labelFor = () => '', get_parent_repe = field => field.copy ? `block---${field.copy.name}` : '';
    let lastField = null;
    %s
    eval(process.argv[1]);
    console.log(JSON.stringify({checked: radios.filter(candidate => candidate.checked).map(candidate => (candidate.copy?.name ?? 'mimo') + ':' + candidate.value),
                                forms: radios.map(candidate => candidate.getAttribute('form')), sent}));
    """

    def page_after(self, call):
        page = app.SHELL
        parts = [re.search(pattern, page, re.S).group(0) for pattern in (
            r"  const SECTION_COPY = .*?window\.fill_inputs = .*?\n  \};", r"  function applyDefault\(.*?\n  \}")]
        script = self.SCRIPT % "\n".join(parts)
        return json.loads(subprocess.run(["node", "-e", script, "--", call], capture_output=True, text=True, check=True).stdout)

    def test_each_copy_shows_its_own_value(self):
        page = self.page_after("fill_inputs(null, {mimo: 'y', sekce: [{r: 'b'}, {r: 'a'}]})")
        self.assertEqual(page["checked"], ["mimo:y", "sekce-rep-0:b", "sekce-rep-1:a"])

    def test_empty_value_leaves_no_button_of_the_copy_chosen(self):
        page = self.page_after("fill_inputs(null, {sekce: [{r: 'a'}, {r: 'a'}]}); fill_inputs(null, {sekce: [{r: 'a'}, {r: ''}]})")
        self.assertEqual(page["checked"], ["sekce-rep-0:a"])

    def test_each_copy_is_a_group_of_its_own(self):
        forms = self.page_after("fill_inputs(null, {})")["forms"]
        self.assertEqual(forms[:2], [None, None])
        self.assertEqual(forms[2], forms[3])
        self.assertEqual(forms[4], forms[5])
        self.assertNotEqual(forms[2], forms[4])

    def test_copy_cloned_with_the_group_of_its_original_gets_its_own(self):
        clone = ("fill_inputs(null, {}); copies[1] = {name: 'sekce-rep-1'}; radios.slice(4).forEach(button => { button.copy = copies[1]; button.setAttribute('form', radios[2].getAttribute('form')); }); "
                 "fill_inputs(null, {})")
        forms = self.page_after(clone)["forms"]
        self.assertNotEqual(forms[2], forms[4])

    def test_value_by_name_alone_goes_to_the_first_group_and_clears_its_choice(self):
        page = self.page_after("fill_inputs(null, {mimo: 'x', sekce: [{r: 'a'}, {r: 'a'}]}); set_radio_value('r', 'b'); set_radio_value('mimo', '')")
        self.assertEqual(page["checked"], ["sekce-rep-0:b", "sekce-rep-1:a"])

    def test_default_value_is_stored_for_the_copy_of_the_field(self):
        page = self.page_after("fill_inputs(null, {sekce: [{r: 'b'}, {r: 'b'}]}); lastField = radios[5]; applyDefault({field: 'r', value: 'a'})")
        self.assertEqual(page["checked"], ["sekce-rep-0:b", "sekce-rep-1:a"])
        self.assertEqual(page["sent"], [{"type": "store_val", "data": {"field": "r", "value": "a", "repe": "block---sekce-rep-1", "input_id": "r-b"}}])


@unittest.skipUnless(shutil.which("node"), "needs Node.js; CI runners have it")
class SavedFileNames(unittest.TestCase):
    """Runs plainName from ui/files.js: a name the hosts refuse would drop the whole save request silently."""

    def test_every_name_becomes_one_the_hosts_accept(self):
        names = ["smlouva: v2.pdf", "složka/příloha.pdf", "a:b.pdf", "C:\\dokumenty\\x.pdf", "slozka/", ".", "..", "a/..",
                 "", "x\x00y.txt", "tab\tname.txt", "obyčejný název.pdf"]
        source = (ROOT / "ui/files.js").read_text(encoding="utf-8")
        block = re.search(r"  const plainName = .*?\n  \};", source, re.S).group(0)
        script = f"{block}\nconsole.log(JSON.stringify(JSON.parse(process.argv[1]).map(plainName)));"
        out = subprocess.run(["node", "-e", script, "--", json.dumps(names)], capture_output=True, text=True, check=True).stdout
        plain = json.loads(out)
        self.assertTrue(all(desktop.is_plain_file_name(name) for name in plain), plain)
        self.assertEqual(plain[:6], ["smlouva_ v2.pdf", "příloha.pdf", "a_b.pdf", "x.pdf", "soubor", "soubor"])
        self.assertEqual(plain[-1], "obyčejný název.pdf")


@unittest.skipUnless(shutil.which("node"), "needs Node.js; CI runners have it")
class RevocationVerdict(unittest.TestCase):
    """Runs the decision in ui/bridge.js: the certificate below was revoked on 1 June."""
    BEFORE, AFTER = "2026-01-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"
    SCRIPT = """
    const window = {}, fetch = async () => ({ok: true, json: async () => ({c: {status: 'revoked', revoked_at: '2026-06-01T00:00:00+00:00'}})});
    %s
    const sigs = JSON.parse(process.argv[1]), r = window.zfoRevocation;
    r.ask('s', sigs, () => {});
    setTimeout(() => console.log(JSON.stringify(sigs.map(sig => [r.provenBeforeRevocation(sig), r.revoked(sig), r.revokedBeforeSigning(sig)]))), 50);
    """

    def verdict(self, kind, proof_at, signed_at):
        source = (ROOT / "ui/bridge.js").read_text(encoding="utf-8")
        block = re.search(r"window\.zfoRevocation = .*?\n\}\)\(\);", source, re.S).group(0)
        sig = {"certificate": "c", "signed_at": signed_at, "proof": {"kind": kind, "at": proof_at} if kind else None}
        out = subprocess.run(["node", "-e", self.SCRIPT % block, "--", json.dumps([sig])], capture_output=True, text=True, check=True).stdout
        return tuple(json.loads(out)[0])

    def test_stamp_before_revocation_keeps_the_signature_valid(self):
        for kind in ("signature", "document"):
            self.assertEqual(self.verdict(kind, self.BEFORE, self.AFTER), (True, False, False))

    def test_signature_stamp_after_revocation_is_invalid(self):
        self.assertEqual(self.verdict("signature", self.AFTER, self.BEFORE), (False, True, True))

    def test_later_document_stamp_leaves_the_signers_claim(self):
        self.assertEqual(self.verdict("document", self.AFTER, self.BEFORE), (False, True, False))
        self.assertEqual(self.verdict("document", self.AFTER, self.AFTER), (False, True, True))

    def test_without_a_stamp_the_signers_claim_decides(self):
        self.assertEqual(self.verdict(None, None, self.BEFORE), (False, True, False))
        self.assertEqual(self.verdict(None, None, self.AFTER), (False, True, True))


class FormScripts(unittest.TestCase):
    """samples/skripty/vypocty.zfo comes from tools/make-script-samples.py, shaped like what offices publish."""

    def setUp(self):
        self.doc = engine.open_document(zipfile.ZipFile(ROOT / "samples/skripty/vypocty.zfo").read("form.fo"))

    def value(self, name):
        return self.doc._get_data_root().xpath(f"/d:root/d:{name}", namespaces=NS)[0].text or ""

    def fill(self, name, value):
        return engine.handle(self.doc, "store_val", {"field": name, "value": value, "input_id": name, "repe": ""})

    def problems(self):
        return engine.handle(self.doc, "check_form", "")[0]

    def test_filled_check_follows_every_branch(self):
        engine.handle(self.doc, "recalc_form", "")
        self.assertEqual(self.value("vyplneno"), "1")
        self.fill("jmeno", "Jana")
        self.assertEqual(self.value("vyplneno"), "0")
        self.fill("tisk", "0")
        self.fill("jmeno", "")
        self.assertEqual(self.value("vyplneno"), "0")

    def test_no_result_leaves_the_value(self):
        self.fill("tisk", "2")
        self.fill("jmeno", "Jana")
        before = self.value("vyplneno")
        self.fill("prijmeni", "Nová")
        self.assertEqual(self.value("vyplneno"), before)

    def test_library_function_joins_fields(self):
        self.fill("jmeno", "Jana")
        self.fill("prijmeni", "Nová")
        self.assertEqual(self.value("cele_jmeno"), "Jana Nová")

    def test_birth_number_gives_a_date_and_a_later_calculation_uses_it(self):
        self.fill("rc", "855815/0007")
        self.assertEqual(self.value("narozen"), "1985-08-15")
        self.assertEqual(self.value("lhuta"), "14.9.1985")

    def test_invalid_birth_number_gives_nothing(self):
        self.fill("rc", "855815/0014")
        self.assertEqual(self.value("narozen"), "")

    def test_a_forms_own_regexp_function_and_the_constructor_coexist(self):
        self.fill("psc", "11000")
        self.assertEqual(self.value("psc_ok"), "true")
        self.fill("psc", "abc")
        self.assertEqual(self.value("psc_ok"), "false")

    def test_constraint_script_is_checked(self):
        self.assertIn("Neplatná hodnota", json.dumps(self.fill("email", "neni-adresa"), ensure_ascii=False))
        self.assertNotIn("Neplatná hodnota", json.dumps(self.fill("email", "jana@example.cz"), ensure_ascii=False))
        self.assertEqual(self.value("email"), "jana@example.cz")
        self.assertEqual(self.problems()["data"], [])

    def test_script_reaching_for_the_network_changes_nothing_and_is_reported(self):
        self.fill("jmeno", "Jana")
        self.assertEqual(self.value("odeslano"), "puvodni")
        self.assertTrue(self.problems()["scripts"])

    def test_failing_expression_leaves_no_helper_in_the_data(self):
        form = FORM_WITH_ATTRIBUTE % '{ Result(GetDocument().GetElement().DataXpath("(((")); }'
        doc = engine.open_document(form.encode())
        engine.handle(doc, "recalc_form", "")
        self.assertNotIn(b"tmp", etree.tostring(doc._get_data_root().getroot()))
        self.assertTrue(doc.script_problems)

    def test_scripts_get_nothing_of_the_application_through_xpath(self):
        for call in ('GetEnvVariable(\\"HOME\\")', "CurrentUserName()", 'SendAppEvent(\\"saveformsign\\")', 'SaveAs(\\"x\\")',
                     'SetAttribute(\\"a\\", \\"b\\")', 'GetListItemData(\\"x\\", concat(\\"Get\\", \\"EnvVariable()\\"), \\"@a\\")'):
            with self.subTest(call):
                form = FORM_WITH_ATTRIBUTE % ('{ Result(GetDocument().GetElement().DataXpath("%s")); }' % call)
                doc = engine.open_document(form.encode())
                engine.handle(doc, "recalc_form", "")
                self.assertEqual(doc._get_data_root().getroot()[0].get("a"), "", call)
                self.assertFalse(doc.script_running)

    def test_form_description_reports_what_does_not_run(self):
        fo = etree.fromstring(zipfile.ZipFile(ROOT / "samples/skripty/vypocty.zfo").read("form.fo"))
        self.assertEqual(app.binds_with_unsupported_scripts(fo), ["odeslano"])
        self.assertEqual(app.count_never_run_scripts(fo), 0)

    def test_button_stores_a_value_to_a_field_saved_as_an_attribute(self):
        doc = engine.open_document((FORM_WITH_ATTRIBUTE % "").encode())
        root = doc._get_data_root().getroot()
        root.set("form602bindref", "a")
        doc.perform_click_action("SetValueToData(vlozeno)")
        self.assertEqual(root[0].get("a"), "vlozeno")
        self.assertEqual(doc._get_computable_data_root().getroot()[0].get("a"), "vlozeno")

    def test_button_tells_the_page_whether_it_changed_the_data(self):
        doc = engine.open_document((FORM_WITH_ATTRIBUTE % "").encode())

        def answer(command):
            return engine.handle(doc, "perform_click", {"command": command})[0]["data"]

        self.assertIn('{"changed": false}', answer("SetValueToData(vlozeno)"))
        doc._get_data_root().getroot().set("form602bindref", "a")
        self.assertIn('{"changed": true}', answer("SetValueToData(vlozeno)"))
        self.assertIn('{"changed": false}', answer("SetValueToData(vlozeno)"))

    def test_xpath_number_is_stored_as_xpath_writes_it(self):
        doc = engine.open_document((FORM_WITH_ATTRIBUTE % "1 + 1").encode())
        engine.handle(doc, "recalc_form", "")
        self.assertEqual(doc._get_data_root().getroot()[0].get("a"), "2")

    def test_cadastre_form_agrees_with_form_filler(self):
        # samples/ref-filler was saved by Form Filler: with tisk=0 all three checks are 0.
        fo = zipfile.ZipFile(ROOT / "samples/cuzk/form.zfo").read("form.fo")
        doc = engine.open_document(fo)
        engine.handle(doc, "recalc_form", "")
        data = doc._get_data_root()
        for name in ("s2a_vyplnene", "s2a_vyplnene2", "s2b_vyplnene"):
            filled = {node.text for node in data.iter(f"{{{data.getroot().nsmap['d']}}}{name}")}
            self.assertEqual(filled, {"0"}, name)
        self.assertEqual(doc.script_problems, set())

    def test_saved_form_keeps_earlier_layers_untouched_by_scripts(self):
        fo = zipfile.ZipFile(ROOT / "samples/cuzk/form.zfo").read("form.fo")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "form.zfo"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("form.fo", fo)
            app.FORMS[path.stem] = path
            session = {"name": path.stem, "doc": engine.open_document(fo)}
            engine.handle(session["doc"], "recalc_form", "")
            saved = etree.fromstring(app.filled_form(session), etree.XMLParser(huge_tree=True))
            layer = etree.tostring(app.publisher_layer(saved).find("{http://www.w3.org/2000/09/xmldsig#}SignatureValue"))
            original = etree.fromstring(fo, etree.XMLParser(huge_tree=True))
            self.assertEqual(layer, etree.tostring(app.publisher_layer(original).find("{http://www.w3.org/2000/09/xmldsig#}SignatureValue")))
            self.assertEqual(zfo_verify.describe_signature_layers(saved)[0]["intact"], True)


class Interpreter(unittest.TestCase):
    """What matters about zfoengine/js602.py that a form cannot show."""

    def run_script(self, source, answer="", library=()):
        return js602.evaluate(js602.Interpreter(library), source, lambda expression: answer, "/d:root/d:x")

    def test_python_stays_out_of_reach(self):
        for source in ('Result("".__class__)', 'Result([].__class__)', 'Result(Result.__globals__)', 'Result(GetDocument.__self__)',
                       'Result((function(){}).__code__)', 'Result("".constructor)', 'Result(({}).__proto__)', 'Result(new Date().__class__)'):
            with self.subTest(source):
                self.assertEqual(self.run_script("{" + source + "}"), "undefined")

    def test_host_objects_offer_only_what_they_list(self):
        for source in ("GetDocument().SendData()", "GetDocument().Print2PDF()", "GetDocument().__class__", "GetDocument().GetElement().SetData('a', 'b')",
                       "GetDocument().GetElement().props", "GetDocument().x = 1"):
            with self.subTest(source), self.assertRaises(js602.Unsupported):
                self.run_script("{" + source + "}")

    def test_unknown_names_stop_the_script(self):
        for source in ("open('/etc/passwd')", "require('fs')", "eval('1')", "Function('return 1')()", "MessageBox('x')", "new ActiveXObject('x')",
                       "__import__('os')", "print(1)"):
            with self.subTest(source), self.assertRaises(js602.Unsupported):
                self.run_script("{" + source + "}")

    def test_runaway_scripts_are_stopped(self):
        for source in ("while (true) {}", "for (;;) { var a = 1; }", "function f() { f(); } f();", "var a = 'x'; while (true) a += a;",
                       "var a = []; while (true) a.push(1);", "var a = []; a[1000000000] = 1;", "/(a+)+$/.test('" + "a" * 40 + "!');",
                       "/(a|a)*$/.test('" + "a" * 40 + "!');", "/(.*)*x/.test('" + "a" * 3000 + "');", "'abc'.repeat(1e9);", "new Array(1e9);"):
            with self.subTest(source), self.assertRaises((js602.Exhausted, js602.Thrown)):
                self.run_script("{" + source + "}")

    def test_deep_assignments_compile_at_once(self):
        for chain in ("a[" * 60 + "0" + "]=0" * 60, "++a[" * 60 + "0" + "]" * 60, "a[" * 60 + "0" + "]++" * 60, "delete a[" * 50 + "0" + "]" * 50):
            with self.subTest(chain[:12]):
                started = time.monotonic()
                try:
                    self.run_script("{ var a = []; " + chain + "; }")
                except (js602.Unsupported, js602.Thrown, js602.Exhausted):
                    pass
                self.assertLess(time.monotonic() - started, 5)

    def test_big_texts_are_paid_for_by_size(self):
        for source in ('var s = "x".repeat(3000000), b = []; while (true) b.push(s + "y");',
                       'var s = "x".repeat(3000000); while (true) s.toUpperCase();',
                       'var s = "x".repeat(3000000); while (true) s.indexOf("q");',
                       'var s = "x".repeat(3000000); var a = [s]; while (true) a.join();'):
            with self.subTest(source[-25:]), self.assertRaises(js602.Exhausted):
                self.run_script("{" + source + "}")

    def test_one_scripts_variables_do_not_reach_another(self):
        interpreter = js602.Interpreter()
        js602.evaluate(interpreter, '{ var a = "zustava"; }', lambda expression: "", "")
        self.assertEqual(js602.evaluate(interpreter, '{ var a; Result(a + ""); }', lambda expression: "", ""), "undefined")

    def test_unreadable_scripts_are_unsupported(self):
        for source in ("{ var }", "{ if ( }", "{ /(/.test('') }", "{ 'abc }", "{" * 500, "{ a" + "[0" * 500, "{" + "(" * 500, "{ /" + "(" * 200 + "/ }", "\x00", "{ @ }"):
            with self.subTest(source[:20]), self.assertRaises(js602.Unsupported):
                self.run_script(source)

    def test_broken_library_script_does_not_stop_the_others(self):
        self.assertEqual(self.run_script("{ Result(dobra()); }", library=("function ( {", "function dobra() { return 'ano'; }")), "ano")

    def test_is_script_tells_xpath_from_javascript(self):
        for value in ("{ var a = 1; }", "  { Result(1); }", "{GetDocument()}", "{\nfunction f() {}\n}"):
            self.assertTrue(js602.is_script(value), value)
        for value in ("{../d:a}", "../d:a + 1", 'If(DataXpath({../d:a}), 1, 2)', "", "{Result}", "{../d:function}", "{d:var x}", "{../d:return}"):
            self.assertFalse(js602.is_script(value), value)


@unittest.skipUnless(shutil.which("node"), "needs Node.js; CI runners have it")
class InterpreterAgainstNode(unittest.TestCase):
    """Our own expressions in both engines: what the language means is not this project's to decide."""

    CASES = r"""
    1 + 2; "1" + 2; 1 + "2"; "3" * "4"; "a" * 1; 7 % 3; -7 % 3; 5.5 % 2; 1 / 0; -1 / 0; 0 / 0; 0.1 + 0.2; 1e21; 1e-7; 123456789012345680000; 1.5e300 * 1e300;
    "" == 0; "0" == 0; null == undefined; null == 0; undefined == 0; "1" == 1; true == 1; true == "1"; [] == ""; [1] == 1; "a" === "a"; 1 === 1.0; NaN == NaN; null === null;
    "a" < "b"; "10" < "9"; "10" < 9; null >= 0; undefined >= 0; NaN < 1; "abc" < "abd";
    !""; !"0"; !0; !null; ![]; !!{}; "" || "x"; "a" && "b"; 0 || null; 1 && 0;
    1 & 3; 1 | 2; 5 ^ 1; ~5; 1 << 3; -8 >> 1; -8 >>> 28; 2147483648 | 0; 1.9 | 0;
    typeof 1; typeof "a"; typeof null; typeof undefined; typeof []; typeof {}; typeof function(){}; typeof neni;
    parseInt("12px"); parseInt("08"); parseInt("0x1f"); parseInt("z"); parseInt("101", 2); parseInt(""); parseInt("-3.9"); parseFloat("3.14abc"); parseFloat(".5"); parseFloat("x"); Number(""); Number(" 12 "); Number("1e3"); Number("12px"); Number(null); Number(true); Number("0x10");
    isNaN("a"); isNaN("1"); isFinite("1"); isFinite(1/0); String(1); String(null); String(undefined); String(true); String([1,2]); Boolean(""); Boolean("0");
    (1.005).toFixed(2); (2.5).toFixed(0); (1.45).toFixed(1); (0).toFixed(2); (1234.5678).toFixed(1); (255).toString(16); (10).toString(2);
    Math.floor(-1.5); Math.ceil(-1.5); Math.round(2.5); Math.round(-2.5); Math.trunc(-1.5); Math.abs(-1); Math.max(1, 2); Math.min(); Math.max(1, "a"); Math.pow(2, 10);
    "abc".length; "abc".charAt(1); "abc".charAt(9); "abc"[1]; "abc".charCodeAt(0); "abcabc".indexOf("c"); "abcabc".indexOf("c", 3); "abcabc".lastIndexOf("a"); "abcabc".lastIndexOf("a", 2); "abc".indexOf("");
    "abcdef".substring(1, 3); "abcdef".substring(3, 1); "abcdef".substring(-1, 2); "abcdef".substring(4); "abcdef".substr(1, 2); "abcdef".substr(-2); "abcdef".slice(1, -1); "abcdef".slice(-2); "abcdef".slice(3, 1);
    "AbC".toLowerCase(); "AbC".toUpperCase(); "  a ".trim(); "a,b,,c".split(","); "abc".split(""); "a1b2c".split(/[0-9]/); "a1b".split(/([0-9])/); "abc".split(); "".split(","); "a,b".split(",", 1); "a".concat("b", 1);
    "aXbX".replace("X", "-"); "aXbX".replace(/X/, "-"); "aXbX".replace(/X/g, "-"); "abc".replace(/(a)(b)/, "$2$1"); "abc".replace(/b/, "[$&]"); "a.b".replace(/\./g, ""); "abc".replace(/b/, function(m) { return m + m; }); "$".replace("$", "$$");
    "abc".match(/b/)[0]; "abab".match(/a/g).length; "abc".match(/x/); "abc".search(/c/); /a/.test("cat"); /^a/.test("cat"); /t$/.test("cat"); /A/i.test("cat"); /^\d+$/.test("123"); /^\d+$/.test("12a"); /\bcat\b/.test("a cat b"); /^\w+$/.test("čau"); /\s/.test("a b");
    /^[0-9]{6}\/[0-9]{3,4}$/.test("123456/7890"); /^[1-7][0-9]{4}$/.test("11000"); /^(\-|\+)?([0-9]+|Infinity)$/.test("-5"); /(a)|(b)/.exec("b")[2]; /(a)|(b)/.exec("b")[1]; /a{2}/.test("aa"); /a{2,}/.test("a"); /a{,2}/.test("a{,2}"); /[A-z]/.test("_"); /[^0-9]/.test("5"); /a.c/.test("a\nc"); /a(?=b)/.test("ab"); /a(?!b)/.test("ab"); /(a)\1/.test("aa"); /a*?b/.exec("aab")[0]; /\//.test("/"); /[\/]/.test("/"); /[a\-z]/.test("-");
    (function() { var r = /a/g; r.test("aa"); return r.lastIndex; })(); (function() { var r = /a/g; var n = 0; while (r.exec("aXa")) n++; return n; })(); new RegExp("^[0-9]+$").test("12"); new RegExp("a", "i").test("A");
    [1, 2, 3].length; [1, 2, 3].join("-"); [1, [2, 3]].join(); [null, undefined, 1].join(); [3, 1, 2].sort().join(); [10, 9, 1].sort().join(); [3, 1].sort(function(a, b) { return a - b; }).join(); [1, 2].concat(3, [4]).join(); [1, 2, 3].slice(1).join(); [1, 2, 3].indexOf(2); [1, 2, 3].reverse().join(); [1, 2, 3].pop(); [1, 2, 3].shift(); [1, 2, 3].map(function(x) { return x * 2; }).join(); [1, 2, 3].filter(function(x) { return x > 1; }).length;
    (function() { var a = [1, 2, 3]; a.splice(1, 1); return a.join(); })(); (function() { var a = []; a[3] = 1; return a.length; })(); (function() { var a = [1]; a.push(2, 3); return a.length; })(); (function() { var a = [1, 2]; a.length = 1; return a.join(); })(); Array(3).length; new Array(1, 2).length; Array.isArray([]);
    ({a: 1}).a; ({a: 1}).b; ({"x y": 2})["x y"]; (function() { var o = {}; o.a = 1; o["b"] = 2; var s = ""; for (var k in o) s += k; return s; })(); (function() { var s = ""; for (var i in [5, 6]) s += i; return s; })(); ("a" in {a: 1}); JSON.stringify({a: [1, "x", null, true]}); JSON.parse('{"a": [1, {"b": 2}]}').a[1].b;
    (function() { var d = new Date(2024, 0, 31); d.setMonth(1); return d.getMonth() + "/" + d.getDate(); })(); new Date(2024, 1, 29).getDate(); new Date(2023, 1, 29).getDate(); new Date(2024, 11, 31).getDay(); new Date(99, 0, 1).getFullYear(); (function() { var d = new Date(2024, 0, 1); d.setDate(d.getDate() - 1); return d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate(); })(); new Date("x").getTime(); new Date("2024-02-29").getDate();
    (function() { var i = 0, s = ""; do { s += i; i++; } while (i < 3); return s; })(); (function() { var s = ""; for (var i = 0; i < 5; i++) { if (i == 1) continue; if (i == 3) break; s += i; } return s; })(); (function() { switch ("b") { case "a": return 1; case "b": case "c": return 2; default: return 3; } })(); (function() { var s = ""; switch (2) { case 1: s += "1"; case 2: s += "2"; case 3: s += "3"; break; default: s += "d"; } return s; })(); (function() { switch (9) { default: return "d"; case 1: return "1"; } })();
    (function() { var f = function(n) { return n < 2 ? 1 : n * f(n - 1); }; return f(5); })(); (function(a, b) { return arguments.length + "" + b; })(1); (function() { return typeof x; var x = 1; })(); (function() { return g(); function g() { return "hoisted"; } })(); (function() { var c = 0; function add() { c++; } add(); add(); return c; })(); (function() { var o = {v: 7, f: function() { return this.v; }}; return o.f(); })();
    (function() { try { throw "x"; } catch (e) { return e + "!"; } })(); (function() { try { null.a; } catch (e) { return "caught"; } })(); (function() { var s = ""; try { s += "t"; } finally { s += "f"; } return s; })();
    (function() { var a = 1; a += 2; a *= 3; a -= 1; a /= 2; a %= 3; return a; })(); (function() { var i = 1; return i++ + ++i; })(); (function() { var s = "a"; s += 1; return s; })(); (function() { var o = {n: 1}; o.n++; o["n"] += 1; return o.n; })(); (1, 2); void 0; true ? "y" : "n"; "" ? "y" : "n";
    """

    @staticmethod
    def cases():
        # the list above is split by a small reader of our own text, not of a form's: it only has to know quotes and brackets
        found, depth, start, quote, at = [], 0, 0, None, 0
        text = InterpreterAgainstNode.CASES
        while at < len(text):
            c = text[at]
            if quote:
                if c == "\\":
                    at += 1
                elif c == quote or (quote == "/" and c == "\n"):
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "/" and text[at - 1] in " (,=!" and text[at + 1] not in " ":
                quote = "/"
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == ";" and depth == 0:
                found.append(text[start:at].strip())
                start = at + 1
            at += 1
        return [case for case in found if case]

    def test_same_answers(self):
        cases = self.cases()
        self.assertGreater(len(cases), 200)
        show = "function s(v){return Array.isArray(v)?'['+v.map(s).join('|')+']':String(v)}"
        lines = "".join(f"try{{out.push(s(eval({json.dumps('(' + case + ')')})))}}catch(e){{out.push('!'+e.name)}}\n" for case in cases)
        script = f"var out=[];{show}\n{lines}console.log(JSON.stringify(out))"
        expected = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, env={**os.environ, "TZ": "UTC"}).stdout)

        def shown(value):
            if isinstance(value, js602.ScriptArray):
                return "[" + "|".join(shown(item) for item in value.items) + "]"
            return js602.to_string(value)

        interpreter = js602.Interpreter()
        wrong = []
        for case, wanted in zip(cases, expected, strict=True):
            box = []
            try:
                keep = js602.NativeFunction(lambda this, args, kept=box: kept.append(args[0]))
                interpreter.run(f"__keep(({case}));", {"__keep": keep})
                got = shown(box[0])
            except js602.Thrown as error:
                got = "!" + js602.to_string(error.value.props["name"])
            if got != wanted:
                wrong.append(f"{case}: node {wanted!r}, here {got!r}")
        self.assertEqual(wrong, [])


class PdfFormData(unittest.TestCase):
    SOURCE = ROOT / "samples-pdf/zadost.pdf"
    VALUES = {"jmeno": "Jan Žluťoučký (ml.) \\ a závorka )", "souhlas": True, "doruceni": "posta", "ucel": "Pro soud"}
    KEY, CERT = SignatureFieldNames.KEY, SignatureFieldNames.CERT

    def setUp(self):
        app.FORMS["pdf-data-test"] = self.SOURCE
        self.addCleanup(app.FORMS.pop, "pdf-data-test", None)

    def session(self, pdf=None):
        return {"name": "pdf-data-test", "pdf": pdf or self.SOURCE.read_bytes()}

    def exported(self, pdf=None, values=VALUES):
        return app.export_pdf_data(self.session(pdf), values)

    def test_both_formats_bring_the_values_back(self):
        contents = self.exported()["contents"]
        for extension in ("xfdf", "fdf"):
            with self.subTest(extension=extension):
                result = app.import_pdf_data(self.session(), contents[extension].encode("utf-8"))
                self.assertEqual({name: result["values"][name] for name in self.VALUES}, self.VALUES)
                self.assertEqual(result["values"]["email"], "")
                self.assertEqual((result["missing"], result["unchangeable"], result["not_offered"], result["several_values"]), ([], [], [], []))

    def test_export_names_the_pdf_and_holds_saved_values(self):
        exported = self.exported(values={})
        self.assertEqual(exported["name"], "pdf-data-test-data.xfdf")
        self.assertIn('href="zadost.pdf"', exported["contents"]["xfdf"])
        self.assertIn("/F (zadost\\056pdf)", exported["contents"]["fdf"])
        self.assertEqual(app.import_pdf_data(self.session(), exported["contents"]["fdf"].encode())["values"]["ucel"], "Pro banku")
        self.assertTrue(exported["contents"]["fdf"].isascii())

    def test_names_of_nested_fields_are_kept(self):
        values = {"osoba.jmeno": "Jan", "osoba.adresa.obec": "Brno", "osoba": "kořen"}
        for write in (pdf_form_data.write_xfdf, pdf_form_data.write_fdf):
            with self.subTest(write=write.__name__):
                self.assertEqual(pdf_form_data.read_form_data(write(values, "x.pdf").encode()), values)

    def test_fields_missing_in_the_form_are_listed(self):
        data = pdf_form_data.write_xfdf({"jmeno": "Jan", "stare_pole": "x", "jine.pole": "y"}, "x.pdf").encode()
        result = app.import_pdf_data(self.session(), data)
        self.assertEqual(result["values"], {"jmeno": "Jan"})
        self.assertEqual(sorted(result["missing"]), ["jine.pole", "stare_pole"])

    def test_values_the_form_does_not_offer_are_listed(self):
        data = pdf_form_data.write_xfdf({"ucel": "Pro úřad", "doruceni": "kurýrem", "souhlas": "Ne", "jmeno": "Jan"}, "x.pdf").encode()
        result = app.import_pdf_data(self.session(), data)
        self.assertEqual(result["values"], {"jmeno": "Jan"})
        self.assertEqual(result["not_offered"], [{"field": "ucel", "value": "Pro úřad"}, {"field": "doruceni", "value": "kurýrem"},
                                                 {"field": "souhlas", "value": "Ne"}])

    def test_unchecked_states_clear_buttons(self):
        for unchecked in ("Off", ""):
            with self.subTest(unchecked=unchecked):
                data = pdf_form_data.write_xfdf({"souhlas": unchecked, "doruceni": unchecked}, "x.pdf").encode()
                result = app.import_pdf_data(self.session(), data)
                self.assertEqual((result["values"], result["not_offered"]), ({"souhlas": False, "doruceni": unchecked}, []))

    def test_several_values_for_one_field_are_listed(self):
        for write in (pdf_form_data.write_xfdf, pdf_form_data.write_fdf):
            with self.subTest(write=write.__name__):
                result = app.import_pdf_data(self.session(), write({"ucel": ["Pro soud", "Ostatní"], "email": ["a@b.cz"]}, "x.pdf").encode())
                self.assertEqual((result["values"], result["several_values"]), ({"email": "a@b.cz"}, ["ucel"]))

    def test_read_only_and_signature_fields_are_not_changed(self):
        writer = PdfWriter(clone_from=self.SOURCE)
        for annotation in writer.pages[0]["/Annots"]:
            if annotation.get_object().get("/T") == "jmeno":
                annotation.get_object()[NameObject("/Ff")] = NumberObject(pdf_form_data.FIELD_FLAG_READ_ONLY)
        out = io.BytesIO()
        writer.write(out)
        data = pdf_form_data.write_xfdf({"jmeno": "Jan", "podpis_zadatele": "x", "email": "a@b.cz"}, "x.pdf").encode()
        result = app.import_pdf_data(self.session(out.getvalue()), data)
        self.assertEqual(result["values"], {"email": "a@b.cz"})
        self.assertEqual(sorted(result["unchangeable"]), ["jmeno", "podpis_zadatele"])

    def signed_with(self, **options):
        writer = IncrementalPdfFileWriter(io.BytesIO(self.SOURCE.read_bytes()), strict=False)
        metadata = signers.PdfSignatureMetadata(**{"field_name": "podpis_zadatele", "md_algorithm": "sha256", **options.pop("metadata", {})})
        signer = signers.PdfSigner(metadata, pdf_sign.ExternalKeySigner(self.KEY, self.CERT), **options)
        return signer.sign_pdf(writer).getvalue()

    def filled_after_signing(self, values):
        signed = pdf_sign.sign_pdf(self.SOURCE.read_bytes(), self.KEY, self.CERT)
        data = pdf_form_data.write_xfdf(values, "zadost.pdf").encode()
        filled = pdf_sign.fill_form_fields(signed, app.import_pdf_data(self.session(signed), data)["values"])
        self.assertTrue(filled.startswith(signed))
        return filled, pdf_sign.describe_signatures(filled)[0]

    def test_values_loaded_into_a_signed_pdf_keep_the_signature_intact(self):
        filled, found = self.filled_after_signing(self.VALUES)
        self.assertTrue(found["intact"])
        self.assertEqual(PdfReader(io.BytesIO(filled)).get_fields()["jmeno"]["/V"], self.VALUES["jmeno"])

    def test_text_and_checkbox_loaded_after_signing_count_as_form_filling(self):
        _, found = self.filled_after_signing({"jmeno": "Jan", "email": "a@b.cz", "souhlas": True})
        self.assertTrue(found["intact"])
        self.assertFalse(found["modified"])

    def test_fields_locked_by_a_signature_are_not_changed(self):
        locked = self.signed_with(metadata={"field_name": "zamek"},
                                  new_field_spec=fields.SigFieldSpec("zamek", field_mdp_spec=fields.FieldMDPSpec(
                                      fields.FieldMDPAction.INCLUDE, ["jmeno"])))
        result = app.import_pdf_data(self.session(locked), self.exported()["contents"]["xfdf"].encode())
        self.assertIn("jmeno", result["unchangeable"])
        self.assertNotIn("jmeno", result["values"])
        self.assertEqual(result["values"]["ucel"], "Pro soud")

    def test_pdf_certified_against_changes_is_refused(self):
        certified = self.signed_with(metadata={"certify": True, "docmdp_permissions": fields.MDPPerm.NO_CHANGES})
        with self.assertRaisesRegex(ValueError, "nedovoluje žádné další změny"):
            app.import_pdf_data(self.session(certified), self.exported()["contents"]["xfdf"].encode())

    def test_other_files_get_a_message(self):
        for data in (b"", b"plain text", b"<html/>", b"%FDF-1.2\n%%EOF"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                app.import_pdf_data(self.session(), data)


class DesktopDataFileDialog(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.window = mock.Mock()

    def saved_file(self, *chosen_names):
        self.window.create_file_dialog.side_effect = [str(self.folder / name) for name in chosen_names]
        bridge = desktop.Bridge(mock.Mock(), mock.Mock())
        bridge._save_file_from_dialog(self.window, {"name": "zadost-data.xfdf", "folder": str(self.folder), "content": "XFDF",
                                                    "contents": {"xfdf": "XFDF", "fdf": "FDF"}})
        self.assertEqual(self.window.create_file_dialog.call_args.kwargs["file_types"], ("XFDF (*.xfdf)", "FDF (*.fdf)"))
        return {path.name: path.read_text(encoding="utf-8") for path in self.folder.iterdir()}

    def test_existing_file_with_the_added_extension_is_asked_about_again(self):
        (self.folder / "data.xfdf").write_text("old", encoding="utf-8")
        self.assertEqual(self.saved_file("data", "data.xfdf"), {"data.xfdf": "XFDF"})
        self.assertEqual(self.window.create_file_dialog.call_args.kwargs["save_filename"], "data.xfdf")

    def test_content_follows_the_chosen_extension(self):
        self.assertEqual(self.saved_file("data.fdf"), {"data.fdf": "FDF"})
        self.assertEqual(self.saved_file("data.XFDF"), {"data.fdf": "FDF", "data.XFDF": "XFDF"})

    def test_name_without_a_known_extension_gets_the_default_one(self):
        self.assertEqual(self.saved_file("data"), {"data.xfdf": "XFDF"})


class DesktopAttachmentSaving(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        port = self.server.server_address[1]
        self.session = app.new_session({"name": "zprava", "message": isds_message.read_message(isds_sample("prijata"))})
        self.window = mock.Mock()
        self.window.get_current_url.return_value = f"http://127.0.0.1:{port}/"
        self.bridge = desktop.Bridge(mock.Mock(), mock.Mock(home=f"http://127.0.0.1:{port}/", window=self.window))

    def files(self, *indexes):
        names = isds_message.read_message(isds_sample("prijata")).info["attachments"]
        return [{"url": f"/attachment?s={self.session}&i={index}", "name": names[index]["name"]} for index in indexes]

    def test_attachment_url_rule(self):
        self.assertTrue(desktop.attachment_url_is_allowed("/attachment?s=Ab_c-9&i=12"))
        for url in ("http://evil.example/attachment?s=x&i=1", "/pdf?s=x", "/attachment?s=x&i=1&j=2", "/attachment?s=x&i=1#",
                    "//evil.example/attachment?s=x&i=1", "/attachment?s=x&i=-1", 5, None):
            with self.subTest(url=url):
                self.assertFalse(desktop.attachment_url_is_allowed(url))

    def test_free_target_never_overwrites(self):
        (self.folder / "a.pdf").write_bytes(b"1")
        (self.folder / "a (2).pdf").write_bytes(b"2")
        self.assertEqual(desktop.free_target(self.folder, "a.pdf"), self.folder / "a (3).pdf")
        self.assertEqual(desktop.free_target(self.folder, "b"), self.folder / "b")
        for name in ("..", ".", "a/b", "a\\b", "a\x00b", "", "C:x.txt"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                desktop.free_target(self.folder, name)

    def test_desktop_saves_one_attachment_where_the_user_chose(self):
        self.window.create_file_dialog.return_value = str(self.folder / "jinak.pdf")
        self.bridge.message({"action": "saveAttachment", "files": self.files(0), "folder": False})
        self.assertEqual(self.window.create_file_dialog.call_args.kwargs["save_filename"], "rozhodnuti.pdf")
        self.assertEqual((self.folder / "jinak.pdf").read_bytes(), b"%PDF-1.4\n% TEST priloha\n")
        self.window.evaluate_js.assert_called_with(f"window.zfoAttachmentSaved && zfoAttachmentSaved({json.dumps(str(self.folder / 'jinak.pdf'))})")

    def test_desktop_saves_all_into_the_folder(self):
        (self.folder / "data.xml").write_bytes(b"older")
        self.window.create_file_dialog.return_value = (str(self.folder),)
        self.bridge.message({"action": "saveAttachment", "files": self.files(0, 1, 2), "folder": True})
        self.assertEqual(self.window.create_file_dialog.call_args.args[0], self.bridge._app.webview.FileDialog.FOLDER)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["data (2).xml", "data.xml", "evil.txt", "rozhodnuti.pdf"])
        self.assertEqual((self.folder / "data.xml").read_bytes(), b"older")
        self.window.evaluate_js.assert_called_with(f"window.zfoAttachmentSaved && zfoAttachmentSaved({json.dumps(str(self.folder))})")

    def test_desktop_reports_a_failed_save_on_the_page(self):
        self.window.create_file_dialog.return_value = str(self.folder / "chybi" / "x.pdf")
        self.bridge.message({"action": "saveAttachment", "files": self.files(0), "folder": False})
        self.assertIn("zfoAttachmentFailed", self.window.evaluate_js.call_args.args[0])

    def test_desktop_refuses_foreign_urls_and_names(self):
        self.window.create_file_dialog.return_value = (str(self.folder),)
        for files in ([{"url": "http://evil.example/x", "name": "x"}], [{"url": f"/attachment?s={self.session}&i=0", "name": "../x"}]):
            with self.subTest(files=files):
                self.bridge.message({"action": "saveAttachment", "files": files, "folder": True})
        self.assertEqual(list(self.folder.iterdir()), [])


class DesktopContainerFileSaving(DesktopAttachmentSaving):
    def setUp(self):
        super().setUp()
        self.container = app.new_session({"entries": [b"%PDF-1.7 smlouva", b"data"], "names": ["smlouva.pdf", "data.txt"]})
        self.bridge._app.open_window = mock.Mock()

    def one(self, index=0, name="smlouva.pdf"):
        return [{"url": f"/container-file?s={self.container}&i={index}", "name": name}]

    def test_container_file_url_rule(self):
        self.assertTrue(desktop.attachment_url_is_allowed("/container-file?s=Ab_c-9&i=12"))
        for url in ("/container-file?s=x&i=1&j=2", "//evil.example/container-file?s=x&i=1", "http://127.0.0.1/container-file?s=x&i=1",
                    "/container-file?s=x&i=1#", "/container-file?s=x&i=-1", "/container-files?s=x&i=1",
                    "/container-file?s=x&i=\u0661", "/container-file?s=x&i=1\n"):
            with self.subTest(url=url):
                self.assertFalse(desktop.attachment_url_is_allowed(url))

    def test_saves_a_container_file(self):
        self.window.create_file_dialog.return_value = str(self.folder / "kopie.pdf")
        self.bridge.message({"action": "saveAttachment", "files": self.one(), "folder": False})
        self.assertEqual((self.folder / "kopie.pdf").read_bytes(), b"%PDF-1.7 smlouva")
        self.bridge._app.open_window.assert_not_called()

    def test_open_opens_the_saved_copy_in_a_new_window(self):
        self.window.create_file_dialog.return_value = str(self.folder / "kopie.pdf")
        self.bridge.message({"action": "saveAttachment", "files": self.one(), "folder": False, "open": True})
        self.bridge._app.open_window.assert_called_once_with(self.folder / "kopie.pdf")

    def test_open_with_a_cancelled_dialog_opens_nothing(self):
        self.window.create_file_dialog.return_value = None
        self.bridge.message({"action": "saveAttachment", "files": self.one(), "folder": False, "open": True})
        self.bridge._app.open_window.assert_not_called()

    def test_open_is_refused_for_a_folder_or_several_files(self):
        files = self.one() + self.one(1, "data.txt")
        for request in ({"files": self.one(), "folder": True}, {"files": files, "folder": False}, {"files": files, "folder": True}):
            with self.subTest(request=request):
                self.bridge.message({"action": "saveAttachment", "open": True, **request})
        self.window.create_file_dialog.assert_not_called()
        self.bridge._app.open_window.assert_not_called()

    def test_open_dialog_offers_containers(self):
        self.assertEqual(desktop.OPEN_DIALOG_FILE_TYPES,
                         ("Podporované soubory (*.zfo;*.fo;*.pdf;*.asice;*.sce;*.asics;*.scs)", "Všechny soubory (*.*)"))
        first = desktop.OPEN_DIALOG_FILE_TYPES[0]
        for extension in ("zfo", "fo", "pdf", "asice", "sce", "asics", "scs"):
            self.assertIn(f"*.{extension}", first)
        main = (ROOT / "macapp/main.swift").read_text(encoding="utf-8")
        for extension in ("asice", "sce", "asics", "scs"):
            self.assertIn(f'"{extension}"', main)
        self.assertIn('&i=[0-9]+\\z"#', main)


class LinuxPdfPrinting(unittest.TestCase):
    A4 = (595.28, 841.89)

    def test_page_fills_paper_of_its_shape(self):
        placed = linux_print.fit_page_on_paper(self.A4, (4800, 6800), 600)
        self.assertEqual(placed.rotation, 0)
        self.assertEqual((placed.x, placed.width), (0, 4800))
        self.assertAlmostEqual(placed.y * 2 + placed.height, 6800)

    def test_landscape_page_turns_onto_portrait_paper(self):
        placed = linux_print.fit_page_on_paper((self.A4[1], self.A4[0]), (4800, 6800), 600)
        self.assertEqual(placed.rotation, 90)
        self.assertLessEqual(placed.width, 4800)
        self.assertLessEqual(placed.height, 6800)
        self.assertAlmostEqual(placed.width / placed.height, self.A4[0] / self.A4[1])

    def test_image_resolution_stops_at_the_print_limit(self):
        for printer_dpi, image_dpi in ((1200, linux_print.MAX_PRINT_DPI), (150, 150)):
            with self.subTest(printer_dpi=printer_dpi):
                placed = linux_print.fit_page_on_paper(self.A4, (self.A4[0] / 72 * printer_dpi, self.A4[1] / 72 * printer_dpi), printer_dpi)
                self.assertAlmostEqual(placed.render_scale * linux_print.POINTS_PER_INCH, image_dpi)

    def test_failed_preview_opens_the_file_in_the_system_viewer(self):
        path = Path("/tmp/zfo-tisk-x.pdf")
        with mock.patch.object(linux_print, "show_print_preview", side_effect=ImportError), \
                mock.patch.object(desktop, "open_with_system") as open_with_system:
            desktop.show_print_preview_or_open(path, mock.Mock())
        open_with_system.assert_called_once_with(path)


@unittest.skipUnless(importlib.util.find_spec("pypdfium2"), "pypdfium2 is installed only on Linux")
class LinuxFontSubstitution(unittest.TestCase):
    def setUp(self):
        import pypdfium2.raw as pdfium
        self.pdfium = pdfium
        self.substitution = linux_print.FontSubstitution()
        self.substitution._system = mock.Mock()
        self.substitution._system.contents.MapFont.return_value = 0

    def map_font(self, pitch_family, charset=None):
        charset = self.pdfium.FXFONT_ANSI_CHARSET if charset is None else charset
        return self.substitution._map_font(None, 400, 0, charset, pitch_family, b"Helvetica", None)

    def test_missing_sans_serif_font_gets_liberation_sans(self):
        font = self.map_font(0)
        self.assertTrue(font)
        self.assertGreater(self.substitution._get_font_data(None, font, linux_print.WHOLE_FONT_FILE, None, 0), 0)

    def test_fixed_pitch_serif_and_symbol_fonts_stay_with_pdfium(self):
        for pitch_family, charset in ((self.pdfium.FXFONT_FF_FIXEDPITCH, None), (self.pdfium.FXFONT_FF_ROMAN, None),
                                      (0, self.pdfium.FXFONT_SYMBOL_CHARSET)):
            with self.subTest(pitch_family=pitch_family, charset=charset):
                self.assertEqual(self.map_font(pitch_family, charset), 0)

    def test_installed_system_font_wins(self):
        self.substitution._system.contents.MapFont.return_value = 1234
        self.assertEqual(self.map_font(0), 1234)


if __name__ == "__main__":
    unittest.main(verbosity=2)
