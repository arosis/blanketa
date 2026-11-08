"""Data messages and delivery receipts from data boxes (ISDS)."""
import base64
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from asn1crypto import cms
from cryptography import x509
from lxml import etree
from support import (
    ROOT,
    isds_sample,
    isds_sample_tool,
    page_data,
    ui_source,
)

import app
import isds_message
import zfo_verify


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
        self.assertEqual(page_data(page)["message"]["subject"], "__SIGNATURES__ __SUBMITTED__ __SESSION__ __FILE__")
        self.assertEqual(page_data(page)["signatures"][0]["subject"], "ISDS TEST")

    def test_attachment_text_stays_json(self):
        hostile = "</script><img src=x onerror=alert(1)>"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zprava.zfo"
            path.write_bytes(isds_sample_tool().build_message(attachments=((hostile + ".pdf", hostile, "main", b"%PDF-1.4\n"),)))
            with mock.patch.dict(app.FORMS, {"hostile": path}):
                page = app.render_message_page("hostile")
        self.assertNotIn("<img src=x", page)
        attachment = page_data(page)["message"]["attachments"][0]
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
        page = ui_source("message.js")
        fallback = re.search(r"window\.zfoAction = name => \(actions\[name\] \?\? (\w+)\)", page).group(1)
        self.assertIn("Datová zpráva je jen ke čtení", page.split(f"const {fallback} = ")[1].split("\n")[0])

    def test_message_page_keeps_czech_typography(self):
        page = ui_source("message.html") + ui_source("message.js")
        self.assertNotIn("razítka v pečetěném", page)
        self.assertNotRegex(page, r"\} (B|kB|MB)`")

    def test_message_files_are_recognised_by_their_first_byte(self):
        self.assertTrue(isds_message.looks_like_message(isds_sample("prijata-ber")))
        self.assertFalse(isds_message.looks_like_message(b"PK\x03\x04"))


# Copied from webview.util.parse_file_type: pywebview raises ValueError on anything else, and the tests run without it.
