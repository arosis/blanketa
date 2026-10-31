"""The form description Form Filler shows, barcodes the forms draw themselves, and saving.

    python -I tests/test_app.py
"""
import copy
import io
import json
import os
import re
import shutil
import subprocess
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
from lxml import html as lxml_html
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
        doc = self.parsed('<a form602btnclick="{ SendData(); }"/><a form602onchange=" { x(); }"/><a form602btnclick="SendAppEvent(1)"/>')
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

    def test_xpath_calculation_may_still_ask_for_them(self):
        doc = engine.open_document((FORM_WITH_ATTRIBUTE % "CurrentUserName()").encode())
        engine.handle(doc, "recalc_form", "")
        self.assertNotEqual(doc._get_data_root().getroot()[0].get("a"), None)
        self.assertFalse(doc.script_problems)

    def test_form_description_reports_what_does_not_run(self):
        fo = etree.fromstring(zipfile.ZipFile(ROOT / "samples/skripty/vypocty.zfo").read("form.fo"))
        self.assertEqual(app.binds_with_unsupported_scripts(fo), ["odeslano"])
        self.assertEqual(app.count_never_run_scripts(fo), 1)

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
            if isinstance(value, js602.Arr):
                return "[" + "|".join(shown(item) for item in value.items) + "]"
            return js602.to_string(value)

        interpreter = js602.Interpreter()
        wrong = []
        for case, wanted in zip(cases, expected, strict=True):
            box = []
            try:
                keep = js602.Native(lambda this, args, kept=box: kept.append(args[0]))
                interpreter.run(f"__keep(({case}));", {"__keep": keep})
                got = shown(box[0])
            except js602.Thrown as error:
                got = "!" + js602.to_string(error.value.props["name"])
            if got != wanted:
                wrong.append(f"{case}: node {wanted!r}, here {got!r}")
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
