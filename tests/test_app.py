"""The form description Form Filler shows, barcodes the forms draw themselves, and saving.

    python -I tests/test_app.py
"""
import copy
import io
import os
import re
import sys
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ZFO_OFFLINE"] = "1"  # tests never download trusted lists

import app  # noqa: E402
import barcode  # noqa: E402
import pdf_sign  # noqa: E402

ITF_BY_PATTERN = {pattern: digit for digit, pattern in barcode.ITF_DIGITS.items()}


def decode_itf(widths):
    """Reads Interleaved 2 of 5 element widths back into digits, checking start and stop patterns."""
    assert widths[:4] == [1, 1, 1, 1] and widths[-3:] == [barcode.WIDE, 1, 1], "start or stop pattern"
    body, digits = widths[4:-3], ""
    for i in range(0, len(body), 10):
        pair = body[i:i + 10]
        to_pattern = lambda ws: "".join("w" if w == barcode.WIDE else "n" for w in ws)  # noqa: E731
        digits += ITF_BY_PATTERN[to_pattern(pair[0::2])] + ITF_BY_PATTERN[to_pattern(pair[1::2])]
    return digits


class Barcodes(unittest.TestCase):
    def test_interleaved_2_of_5_round_trip(self):
        for data in ("7582212456", "0000", "1234567"):
            self.assertEqual(decode_itf(barcode.interleaved_2_of_5(data)), data.zfill(len(data) + len(data) % 2))

    def test_svg_size_follows_the_form(self):
        drawing = barcode.svg("interleaved2of5", "7582212456", "8.0mm", "0.4mm", "not")
        self.assertIn('height="8mm"', drawing)
        self.assertNotIn("<text", drawing)
        self.assertIn("<text", barcode.svg("interleaved2of5", "12", "8mm", "0.4mm", "yes"))

    def test_unknown_symbology_falls_back_to_text(self):
        self.assertEqual(barcode.svg_data_uri("code128", "ABC"), "")
        self.assertEqual(barcode.svg_data_uri("interleaved2of5", "12A4"), "")

    def test_form_barcode_is_drawn(self):
        app.FORMS["barcode-test"] = ROOT / "samples/cssz-onz/form.zfo"
        try:
            html = app.render("barcode-test")
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


class UnsupportedForms(unittest.TestCase):
    @staticmethod
    def parsed(xml):
        return etree.fromstring(f'<f xmlns:fm="http://software602.cz/forms">{xml}</f>')

    def test_notice_stays_outside_the_zoomed_page(self):
        # Whatever is not listed as chrome moves into .zfo-page and would scale with the page zoom.
        chrome = re.search(r"const chrome = \[\.\.\.document\.querySelectorAll\('([^']+)'\)", app.SHELL).group(1)
        self.assertIn(".zfo-notice", chrome)

    def test_javascript_calculations_are_reported(self):
        doc = etree.fromstring(zipfile.ZipFile(ROOT / "samples/cuzk/form.zfo").read("form.fo"), etree.XMLParser(huge_tree=True))
        self.assertEqual(app.script_binds(doc), ["s2a_vyplnene2", "s2a_vyplnene", "s2b_vyplnene"])

    def test_form_without_javascript_is_not_reported(self):
        for sample in ("cssz-onz", "olk-dotace"):
            doc = etree.fromstring(zipfile.ZipFile(ROOT / f"samples/{sample}/form.zfo").read("form.fo"), etree.XMLParser(huge_tree=True))
            self.assertEqual(app.script_binds(doc), [])

    def test_braces_alone_are_not_javascript(self):
        doc = self.parsed('<fm:bind Id="x" calculate="{../d:a}"/><fm:bind Id="y" calculate="  { var a = 1; }"/>'
                          '<fm:bind Id="z" constraint="{GetDocument().GetElement()}"/>')
        self.assertEqual(app.script_binds(doc), ["y", "z"])

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
        return pdf_sign.sign(out.getvalue(), self.KEY, self.CERT)

    def test_last_signature_covering_the_file_is_intact(self):
        found = pdf_sign.signatures(self.signed())[0]
        self.assertTrue(found["intact"])
        self.assertTrue(found["covers_whole"])
        self.assertFalse(found["changes_unchecked"])

    def test_later_changes_that_cannot_be_analysed_are_reported(self):
        first, second = pdf_sign.signatures(pdf_sign.sign(self.signed(), self.KEY, self.CERT))
        self.assertTrue(first["intact"])
        self.assertFalse(first["covers_whole"])
        self.assertTrue(first["changes_unchecked"])
        self.assertFalse(first["modified"])
        self.assertFalse(second["changes_unchecked"])

    def test_changed_content_is_still_broken(self):
        signed = self.signed()
        at = signed.index(b"/MediaBox")
        found = pdf_sign.signatures(signed[:at] + b"/MediaBoy" + signed[at + 9:])[0]
        self.assertFalse(found["intact"])
        self.assertFalse(found["changes_unchecked"])

    def test_ordinary_field_is_analysed(self):
        document = pdf_sign.sign(pdf_sign.sign((ROOT / "samples-pdf/zadost.pdf").read_bytes(), self.KEY, self.CERT), self.KEY, self.CERT)
        self.assertFalse(any(found["changes_unchecked"] for found in pdf_sign.signatures(document)))


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
        page = app.render("many-sections")
        session = app.SESSIONS[re.search(r"const SESSION = '([\w-]+)'", page).group(1)]
        for message in ("document.ready", "init_rep_sections", "get_readonly_binds", "get_selected_listboxes",
                        "get_multiswitch_list", "get_repeatable"):
            answer = app.engine.handle(session["doc"], message, "")
        # The engine's share only, usually about two seconds; generous for a loaded CI runner.
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(answer[0]["data"].count('"o1_porad_cislo"'), self.SECTIONS)

    def test_form_is_not_painted_before_the_shell_styles_it(self):
        page = app.render("many-sections")
        self.assertLess(page.index(app.OPENING_STYLE), page.index("<body"))
        self.assertIn("body:not(.zfo-app) > * { display: none; }", app.OPENING_STYLE)

    def test_page_keeps_its_opening_fixes(self):
        # Removing one breaks nothing else, the form just opens slowly again.
        for fix in ("window.get_html_elem =", "window.fill_inputs =", "window.set_multiswitch_controls_callback =",
                    "window.populate_repeating_sections =", "REFRESHES.has(type)", "classList.add('zfo-opening')",
                    "AVAILABLE_WHILE_OPENING.has(name)", "window.send_rep_sections_originals =",
                    "window.set_readonly_inputs_callback =", "window.field_changed_error_callback ="):
            self.assertIn(fix, app.SHELL)

    def test_page_lays_out_only_after_opening(self):
        self.assertNotIn("]:visible", app.SHELL)  # jQuery measures it, and a page still hidden has no visible field


class TimeStamps(unittest.TestCase):
    """samples-pdf/razitka comes from tools/make-timestamp-samples.py."""
    FOLDER = ROOT / "samples-pdf/razitka"

    def read(self, name):
        return pdf_sign.signatures((self.FOLDER / f"{name}.pdf").read_bytes())

    @staticmethod
    def stamp(kind="signature", at="2026-01-15T10:00:00+00:00", intact=True):
        return {"kind": kind, "intact": intact, "at": at if intact else None, "issuer": "TSA", "der": b"certificate"}

    def judge(self, stamps, verdict):
        sig = {"stamps": stamps}
        with mock.patch.object(app.certstore, "stamp_trust", return_value=verdict) as asked:
            app.judged_stamps(sig)
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

    def test_stamp_of_another_document_does_not_break_the_signature(self):
        found = self.read("razitko-jineho-dokumentu")[0]
        self.assertTrue(found["intact"])
        self.assertFalse(found["stamps"][0]["intact"])

    def test_changed_content_breaks_a_document_stamp(self):
        data = (self.FOLDER / "razitko-dokumentu.pdf").read_bytes()
        at = data.index(b"/MediaBox")
        stamp = pdf_sign.signatures(data[:at] + b"/MediaBoy" + data[at + 9:])[1]
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

    def test_good_stamp_hides_a_bad_one(self):
        sig, _ = self.judge([self.stamp(intact=False), self.stamp()], {"qualified": True, "unverifiable": False, "reason": ""})
        self.assertIsNotNone(sig["proof"])
        self.assertEqual(sig["stamp_problem"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
