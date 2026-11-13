"""Opening, describing and filling ZFO forms: what the engine answers the page, and barcodes the forms draw."""
import copy
import io
import json
import os
import re
import tempfile
import time
import unittest
import zipfile
from datetime import datetime
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
    NameObject,
    TextStringObject,
)
from support import NS, ROOT, page_data, ui_source

import app
import barcode
import zfo_verify
from zfoengine import api as engine
from zfoengine import form_scripts

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


SWITCHED_FORM = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:krok>%s</d:krok></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="krok" nodeset="d:krok"/></fm:bind></fm:model></fm:formparams>
<fo:multi-switch form602ct="swi"><fm:data-switch form602ct="dsw" form602bindref="krok"/>
<fo:multi-case case-name="1" form602ct="cas"/><fo:multi-case case-name="2" form602ct="cas"%s/><fo:multi-case case-name="3" form602ct="cas"/>
</fo:multi-switch></fo:root></dsig:Object></dsig:Signature>"""


class SwitchedParts(unittest.TestCase):
    """The value of a switch picks the part of the form shown, as Form Filler picks it."""
    def shown(self, value, starting=""):
        doc = engine.open_document((SWITCHED_FORM % (value, starting)).encode())
        return doc.get_multiswitch_values()["data"][0]["shown"]

    def test_a_value_shows_its_case_and_an_unknown_one_none(self):
        self.assertEqual(self.shown("3"), ["3"])
        self.assertEqual(self.shown("9"), [])

    def test_an_empty_value_shows_the_case_marked_to_start_shown_or_the_first(self):
        self.assertEqual(self.shown("", ' starting-state="show"'), ["2"])
        self.assertEqual(self.shown(""), ["1"])

    def test_a_case_without_a_name_shows_only_for_an_empty_value(self):
        unnamed = SWITCHED_FORM.replace('case-name="1" ', '')
        def shown(value):
            return engine.open_document((unnamed % (value, "")).encode()).get_multiswitch_values()["data"][0]
        self.assertEqual((shown("")["cases"], shown("")["shown"]), (["", "2", "3"], [""]))
        self.assertEqual(shown("2")["shown"], ["2"])

    def test_all_shows_every_case(self):
        self.assertEqual(self.shown("all", ' starting-state="show"'), ["1", "2", "3"])


FORM_WITH_CONDITIONS = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:krok>3</d:krok><d:priloha/><d:jmeno/><d:psc>abc</d:psc></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="krok" nodeset="d:krok"/>
<fm:bind Id="priloha" nodeset="d:priloha" required="((/d:root/d:krok = '3' and /d:root/d:jmeno = '')"/>
<fm:bind Id="jmeno" nodeset="d:jmeno" required="/d:root/d:krok = '3'"/>
<fm:bind Id="psc" nodeset="d:psc" constraint="%s" constraintcomment="PSČ má pět číslic."/></fm:bind></fm:model></fm:formparams>
<fo:inline form602ct="btn" form602bind="krok" form602btnclick="{ GetDocument().CheckData(); }"><![CDATA[Pokračovat]]></fo:inline>
</fo:root></dsig:Object></dsig:Signature>"""


class UnreadableConditions(unittest.TestCase):
    """A condition of a published form that XPath cannot read is left out of the check, which goes on with the rest."""
    def check(self, constraint):
        doc = engine.open_document((FORM_WITH_CONDITIONS % constraint).encode())
        answer = engine.handle(doc, "check_form", "")[0]
        return [problem["field"] for problem in answer["data"]], answer["scripts"]

    def test_the_other_fields_are_checked_and_the_check_says_it_is_not_complete(self):
        self.assertEqual(self.check("string-length(.) = 5"), (["jmeno", "psc"], ["podmínka formuláře, kterou nejde vyhodnotit"]))

    def test_an_unreadable_constraint_is_left_out_too(self):
        self.assertEqual(self.check("string-length(. = 5"), (["jmeno"], ["podmínka formuláře, kterou nejde vyhodnotit"]))

    def test_a_check_the_form_runs_itself_says_it_is_not_complete(self):
        for constraint, fields in (("string-length(.) = 5", ["jmeno", "psc"]), ("true()", ["jmeno"])):
            with self.subTest(constraint):
                doc = engine.open_document((FORM_WITH_CONDITIONS % constraint).encode())
                with self.assertLogs("zfo.zfo_doc", "INFO") as logs:
                    result = form_scripts.run_button_script(doc, 0, "", [])
                self.assertEqual([problem["field"] for problem in result["problems"]], fields)
                self.assertEqual(result["scripts"], ["podmínka formuláře, kterou nejde vyhodnotit"])
                self.assertEqual(result["notices"], [form_scripts.INCOMPLETE_CHECK_NOTICE])
                self.assertIn("((/d:root/d:krok = '3'", logs.output[0])

    def test_a_check_with_readable_conditions_says_nothing_more(self):
        form = (FORM_WITH_CONDITIONS % "true()").replace("required=\"((", "required=\"(")
        result = form_scripts.run_button_script(engine.open_document(form.encode()), 0, "", [])
        self.assertNotIn("scripts", result)
        self.assertEqual(result["notices"], [])


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
        return app.SESSIONS[page_data(page)["session"]]

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
        session = app.SESSIONS[page_data(page)["session"]]
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
            self.assertIn(fix, ui_source("shell.js"))

    def test_page_lays_out_only_after_opening(self):
        self.assertNotIn("]:visible", ui_source("shell.js"))  # jQuery measures it, and a page still hidden has no visible field


class UnsupportedForms(unittest.TestCase):
    @staticmethod
    def parsed(xml):
        return etree.fromstring(f'<f xmlns:fm="http://software602.cz/forms">{xml}</f>')

    def test_notice_stays_outside_the_zoomed_page(self):
        # Whatever is not listed as chrome moves into .zfo-page and would scale with the page zoom.
        chrome = re.search(r"const chrome = \[\.\.\.document\.querySelectorAll\('([^']+)'\)", ui_source("shell.js")).group(1)
        self.assertIn(".zfo-notice", chrome)

    def test_form_with_scripts_that_run_is_not_reported(self):
        for sample in ("cssz-onz", "olk-dotace", "cuzk"):
            doc = etree.fromstring(zipfile.ZipFile(ROOT / f"samples/{sample}/form.zfo").read("form.fo"), etree.XMLParser(huge_tree=True))
            self.assertEqual(app.binds_with_unsupported_scripts(doc), [])

    def test_braces_alone_are_not_javascript(self):
        doc = self.parsed('<fm:bind Id="x" calculate="{../d:a}"/><fm:bind Id="y" calculate="  { var a = SendData(); }"/>'
                          '<fm:bind Id="z" constraint="{GetDocument().SendData()}"/><fm:bind Id="w" calculate="{ var a = 1; }"/>')
        self.assertEqual(app.binds_with_unsupported_scripts(doc), ["y", "z"])

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


CHECK_BOX_GROUP_FORM = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:podava>1</d:podava><d:pripraveno/></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="podava" nodeset="d:podava"/><fm:bind Id="pripraveno" nodeset="d:pripraveno"/></fm:bind>
</fm:model></fm:formparams>
<fo:page-sequence master-reference="p"><fo:flow flow-name="xsl-region-body"><fo:block>
<fo:character form602bind="podava" form602ct="chb" form602on="1" character="x"
 form602onchange="{ GetDocument().GetElement().SetDataTo('pripraveno', 'zadatel ' + GetDocument().GetElement().DataXpath('.')); }"/>
<fo:character form602bind="podava" form602ct="chb" form602on="2" character="x"
 form602onchange="{ GetDocument().GetElement().SetDataTo('pripraveno', 'zastupce ' + GetDocument().GetElement().DataXpath('.')); }"/>
</fo:block></fo:flow></fo:page-sequence></fo:root></dsig:Object></dsig:Signature>"""


class ScriptsOfCheckBoxGroups(unittest.TestCase):
    """Each check box of one bind runs its own change script, as Form Filler runs the script of the box clicked."""
    def test_the_box_clicked_runs_its_script_when_checked_and_unchecked(self):
        doc = engine.open_document(CHECK_BOX_GROUP_FORM.encode())
        for value, prepared in (("2", "zastupce 2"), ("", "zastupce "), ("1", "zadatel 1"), ("", "zadatel ")):
            with self.subTest(value):
                answers = engine.handle(doc, "store_val", {"field": "podava", "value": value, "repe": "", "input_id": "podava"})
                script = next(answer["data"] for answer in answers if str(answer["data"]).startswith("form_script_callback("))
                self.assertEqual(json.loads(script[len("form_script_callback("):-1])["values"]["pripraveno"], prepared)


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

    def test_a_check_box_carries_the_values_it_writes(self):
        self.assertEqual([(box.get("value"), box.get("data-off")) for box in self.inputs("checkbox")], [("1", "0"), ("1", "0")])

    def test_the_value_of_a_check_box_is_stored_as_the_page_sends_it(self):
        doc = engine.open_document(self.FORM.encode())
        for value in ("0", "", "1"):
            engine.handle(doc, "store_val", {"field": "chb", "value": value, "repe": "", "input_id": "chb"})
            self.assertEqual(doc.get_value("chb"), value)

    def test_page_gets_values_of_controls_named_by_bind(self):
        doc = engine.open_document(self.FORM.encode())
        answer = engine.handle(doc, "get_init_values", {"chb": {"type": "check", "bind": "chb"}, "rad": {"type": "radio", "bind": "rad"}})
        values = json.loads(answer[0]["data"][len("set_inputs_init_values_callback("):-1])
        self.assertEqual((values["chb"]["value"], values["rad"]["value"]), ("1", "b"))

    def test_control_without_any_name_does_not_reach_the_page(self):
        # The page builds the selector "#" from an empty key, and that stops it before it reaches the other fields.
        doc = engine.open_document(self.FORM.encode())
        self.assertEqual(self.inputs("radio")[2].get("name"), "")
        answer = engine.handle(doc, "get_init_values", {"": {"type": "radio", "bind": ""}, "chb": {"type": "check", "bind": "chb"}})
        self.assertEqual(list(json.loads(answer[0]["data"][len("set_inputs_init_values_callback("):-1])), ["chb"])


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
        self.assertEqual(self.sections_on_page(), [{"b": "", "c": "ne"}, {"b": "druha", "c": "ano"}])


FIELD_VALUES_FORM = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample"
 xmlns:xs="http://www.w3.org/2001/XMLSchema">
<fm:formparams><fm:model><fm:instance><d:root><d:dat>1.2.1990</d:dat><d:cas>8:30</d:cas><d:psc>110 00</d:psc><d:pocet>x</d:pocet>
<d:den/><d:kod>AB12</d:kod></d:root></fm:instance>
<xs:schema><xs:simpleType name="PscT"><xs:restriction base="xs:string"><xs:pattern value="[0-9]{5}"/></xs:restriction></xs:simpleType>
</xs:schema>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="dat" nodeset="d:dat"/><fm:bind Id="cas" nodeset="d:cas"/>
<fm:bind Id="psc" nodeset="d:psc" type="d:PscT"/><fm:bind Id="pocet" nodeset="d:pocet" type="xs:int" required="true()"/>
<fm:bind Id="den" nodeset="d:den" calculate="&quot;CurrentDate( dd.MM.yyyy )&quot;"/><fm:bind Id="kod" nodeset="d:kod"/></fm:bind>
</fm:model></fm:formparams>
<fo:page-sequence master-reference="p"><fo:flow flow-name="xsl-region-body">
<fo:block form602bind="dat" form602ct="dat" form602format="d.M.yyyy"/><fo:block form602bind="cas" form602ct="tim" form602format="H:mm"/>
<fo:block form602bind="psc" form602ct="txt"/><fo:block form602bind="pocet" form602ct="num" form602format="# ##0,00" form602maxchars="4"/>
<fo:block form602bindref="den" form602ct="out" form602format="d. M. yyyy"/>
<fo:block form602bind="kod" form602ct="txt" form602chars="[0123456789]" form602maxchars="3"/>
</fo:flow></fo:page-sequence></fo:root></dsig:Object></dsig:Signature>"""


class FieldValuesInTheCheck(unittest.TestCase):
    """The check names values a field does not accept, as Form Filler's Checker.Check does; the page shows them by the picture."""

    def setUp(self):
        self.doc = engine.open_document(FIELD_VALUES_FORM.encode())

    def problems(self):
        return {problem["field"]: problem["message"] for problem in engine.handle(self.doc, "check_form", "")[0]["data"]}

    def test_values_the_fields_do_not_accept(self):
        self.assertEqual(self.problems(), {
            "dat": "Hodnota není datum.", "psc": "Hodnota nemá tvar, který formulář vyžaduje.",
            "pocet": "Hodnota není celé číslo v rozsahu, který pole připouští.", "kod": "Hodnota může mít nejvýše 3 znaky.",
        })

    def test_good_values_pass(self):
        for field, value in (("dat", "1990-02-01"), ("psc", "11000"), ("pocet", "12"), ("kod", "123")):
            engine.handle(self.doc, "store_val", {"field": field, "value": value, "repe": "", "input_id": field})
        self.assertEqual(self.problems(), {})

    def test_current_date_follows_its_picture(self):
        engine.handle(self.doc, "recalc_form", "")
        self.assertEqual(self.doc.get_value("den"), datetime.now().strftime("%d.%m.%Y"))

    def test_the_page_gets_the_pictures_and_a_field_for_a_time(self):
        page = lxml_html.fromstring(etree.tostring(app.XSLT(etree.ElementTree(etree.fromstring(FIELD_VALUES_FORM)), PATH_TO_JS="'/s/'",
                                                                 PATH_TO_CSS="'/s/'"), method="html", encoding="unicode"))
        fields = {element.get("name"): (element.get("class"), element.get("data-format"), element.get("readonly") or element.get("maxlength") or None)
                  for element in page.xpath("//*[@data-format]")}
        self.assertEqual(fields, {"dat": ("date-pick", "d.M.yyyy", None), "cas": ("timeInput", "H:mm", None),
                                  "pocet": ("numberInput", "# ##0,00", None), "den": ("outputelement", "d. M. yyyy", None)})


MISCALLED_NOT = "Not( DataXpath(/d:root/d:program[1]='A'), DataXpath(/d:root/d:program[1]='B') )"
FORM_WITH_FUNCTION_CONDITIONS = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:program>%s</d:program><d:castka/><d:jmeno/><d:ucel/></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="program" nodeset="d:program"/>
<fm:bind Id="castka" nodeset="d:castka" required="&quot;And( DataXpath(/d:root/d:program[1]!='A'), DataXpath(/d:root/d:program[1]!='B'), DataXpath(/d:root/d:program[1]!='C'), DataXpath(/d:root/d:program[1]!='D') )&quot;"/>
<fm:bind Id="ucel" nodeset="d:ucel" required="&quot;%s&quot;"/>
<fm:bind Id="jmeno" nodeset="d:jmeno" required="true()"/></fm:bind></fm:model></fm:formparams>
</fo:root></dsig:Object></dsig:Signature>"""


class ConditionsWrittenAsFunctions(unittest.TestCase):
    def check(self, program, condition_of_purpose=MISCALLED_NOT):
        doc = engine.open_document((FORM_WITH_FUNCTION_CONDITIONS % (program, condition_of_purpose)).encode())
        answer = engine.handle(doc, "check_form", "")[0]
        return sorted(problem["field"] for problem in answer["data"]), answer["scripts"]

    def test_and_with_four_parts_holds_when_all_of_them_hold(self):
        self.assertIn("castka", self.check("E")[0])

    def test_and_with_four_parts_fails_on_any_of_them(self):
        for program in "ABCD":
            with self.subTest(program):
                self.assertNotIn("castka", self.check(program)[0])

    def test_a_function_called_with_more_values_than_it_takes_is_left_out_and_the_rest_is_checked(self):
        self.assertEqual(self.check("E"), (["castka", "jmeno"], ["podmínka formuláře, kterou nejde vyhodnotit"]))

    def test_a_function_given_text_for_a_number_is_left_out_and_named_in_the_log(self):
        with self.assertLogs("zfo.zfo_doc", "WARNING") as logs:
            checked = self.check("E", "Round( 'abc' , 2 )")
        self.assertEqual(checked, (["castka", "jmeno"], ["podmínka formuláře, kterou nejde vyhodnotit"]))
        self.assertIn("ValueError", logs.output[0])

    def test_the_sample_names_the_required_amount_the_user_deleted(self):
        doc = engine.open_document(zipfile.ZipFile(ROOT / "samples/olk-dotace/form.zfo").read("form.fo"))
        engine.handle(doc, "store_val", {"field": "z_celkem", "value": "", "repe": "", "input_id": "z_celkem"})
        self.assertIn("z_celkem", [problem["field"] for problem in engine.handle(doc, "check_form", "")[0]["data"]])
