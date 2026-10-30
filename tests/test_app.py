"""The form description Form Filler shows, barcodes the forms draw themselves, and saving.

    python -I tests/test_app.py
"""
import io
import os
import re
import sys
import tempfile
import time
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from lxml import etree
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, NameObject, TextStringObject

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ZFO_OFFLINE"] = "1"  # tests never download trusted lists

import app  # noqa: E402
import barcode  # noqa: E402

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
