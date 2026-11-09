"""Attacks a hostile form or web page could try, checked against the server, the verifier and the hosts' guards."""
import base64
import copy
import hashlib
import http.client
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import warnings
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import quoteattr

import asic_samples
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from lxml import etree
from pypdf import PdfReader
from support import (
    ISDS_SAMPLES,
    ROOT,
    form_with_stylesheets,
    isds_sample_tool,
    page_data,
    stylesheet,
    ui_source,
)

import app
import asic_verify
import certstore
import desktop
import isds_message
import lookups
import pdf_form_data
import public_services
import ruian
import xades_verify
import zfo_verify
from zfoengine import api as engine
from zfoengine import form_scripts

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


class HostileIsdsPages(unittest.TestCase):
    def test_message_page_escapes_values(self):
        hostile = "</script><script>window.pwned=1</script>"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zprava.zfo"
            path.write_bytes(isds_sample_tool().build_message(subject=hostile, sender=hostile,
                                                              attachments=((hostile, "text/html", "main", b"<b>x</b>"),)))
            with mock.patch.dict(app.FORMS, {"hostile-isds": path}):
                page = app.render_message_page("hostile-isds")
        self.assertNotIn("<script>window.pwned", page)
        self.assertIn("\\u003c/script\\u003e", page)
        self.assertNotIn("innerHTML", ui_source("message.js").split("const MESSAGE")[1].split("renderSeal")[0])


class HostileIsdsMessages(unittest.TestCase):
    def assert_refused_quickly(self, data, reason=None):
        started = time.monotonic()
        with self.assertRaises(ValueError) as refused:
            isds_message.read_message(data)
        self.assertLess(time.monotonic() - started, 2)
        if reason:
            self.assertIn(str(refused.exception), reason)

    def test_entities_are_never_expanded(self):
        laughs = (b'<?xml version="1.0"?><!DOCTYPE q [<!ENTITY a "aaaaaaaaaa">' + b"".join(
            f'<!ENTITY {chr(98 + i)} "{("&" + chr(97 + i) + ";") * 10}">'.encode() for i in range(8))
            + b']><q:MessageDownloadResponse xmlns:q="http://isds.czechpoint.cz/v20/message">&i;</q:MessageDownloadResponse>')
        self.assert_refused_quickly(isds_sample_tool().build_message(xml=laughs), {"message content declares a DTD", "message content is not XML"})
        external = (b'<?xml version="1.0"?><!DOCTYPE q [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                    b'<q:MessageDownloadResponse xmlns:q="http://isds.czechpoint.cz/v20/message">&x;</q:MessageDownloadResponse>')
        self.assert_refused_quickly(isds_sample_tool().build_message(xml=external), {"message content declares a DTD"})

    def test_truncated_envelope_is_refused(self):
        data = (ISDS_SAMPLES / "prijata-ber.zfo").read_bytes()
        for end in (1, 2, 10, 100, 1000, 3000, 6000, len(data) // 2, len(data) - 10, len(data) - 1):
            with self.subTest(end=end):
                self.assert_refused_quickly(data[:end])

    def test_huge_declared_length_is_refused(self):
        self.assert_refused_quickly(b"\x30\x88" + (1 << 40).to_bytes(8, "big") + b"\x06\x09" + b"\x00" * 64)

    def test_invalid_base64_attachment_is_refused(self):
        xml = isds_message.read_message((ISDS_SAMPLES / "prijata.zfo").read_bytes()).content
        broken = re.sub(rb"(<p:dmEncodedContent>)[^<]+", rb"\1!!!not base64!!!", xml, count=1)
        self.assertNotEqual(broken, xml)
        self.assert_refused_quickly(isds_sample_tool().build_message(xml=broken), {"attachment is not valid base64"})

    def test_many_attachments_with_one_name(self):
        started = time.monotonic()
        names = isds_message.safe_file_names(["a.pdf"] * 2000)
        self.assertEqual(len(set(name.lower() for name in names)), 2000)
        self.assertLess(time.monotonic() - started, 2)

    def test_message_without_the_seal_certificate_is_refused(self):
        from asn1crypto import cms
        info = cms.ContentInfo.load((ISDS_SAMPLES / "prijata.zfo").read_bytes())
        del info["content"]["certificates"]
        message = isds_message.read_message(info.dump(force=True))
        with self.assertRaises(ValueError):
            isds_message.describe_seal(message)


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


def load_part(content, path):
    return f'var part = new Variant(VarStr, ""); part.format = VarFXML; part.data = "{content}"; GetDocument().LoadFormDataPart(part, "{path}");'


class HostileFormScripts(unittest.TestCase):
    def test_what_reaches_outside_the_form_stops_the_script_and_undoes_it(self):
        for call in ("GetDocument().SendData(1, 2)", "GetDocument().SendDataToEPortal(1)", "GetDocument().SaveData(1)", "GetDocument().Print2PDF(1)",
                     "GetDocument().PrepareSignature(1)", "GetDocument().FinalizeSignature(1)", "GetDocument().ChooseCert()",
                     "GetDocument().Close()", "GetDocument().CloseApp()", "GetDocument().OpenAux('x')", "GetDocument().LoadFormData(1)",
                     "GetDocument().GetElement().OpenLink('http://example.org')", "GetDocument().GetElement().SetBinData('.', 1)",
                     "new XMLHttpRequest()", "eval('1')", "require('os')", "__import__('os')"):
            with self.subTest(call):
                doc = form_running(WRITE + call + ";")
                result = click(doc)
                self.assertIn("refused", result)
                self.assertEqual(value_of_a(doc), "puvodni")
                self.assertFalse(doc.get_value("soucet"))

    def test_only_the_known_register_can_be_asked(self):
        ares = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/00006947"
        for method, url in (("SM_POST", ares), ("SM_GET", ares.replace("https:", "http:")), ("SM_GET", ares + "?x=1"), ("SM_GET", ares + "/../x"),
                            ("SM_GET", ares.replace("ares.gov.cz", "ares.gov.cz.attacker.example")), ("SM_GET", ares.replace("00006947", "0000694")), ("SM_GET", ares.replace("00006947", "٠٠٠٠٦٩٤٧")),
                            ("SM_GET", "https://attacker.example/?ico=00006947"), ("undefined", ares)):
            with self.subTest(method=method, url=url):
                script = WRITE + f"var p = new SendParam(); p.method = {method}; p.url = '{url}'; GetDocument().SendData(new Variant(VarStr, ''), p);"
                doc = form_running(script)
                result = form_scripts.run_button_script(doc, 0, "", [], {url: {"status": 200, "body": "{}"}})
                self.assertEqual(result, {"refused": "SendData"})
                self.assertEqual(value_of_a(doc), "puvodni")

    def test_answers_the_page_did_not_get_from_the_server_are_ignored(self):
        ares = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/00006947"
        script = f"var p = new SendParam(); p.method = SM_GET; p.url = '{ares}'; GetDocument().SendData(new Variant(VarStr, ''), p);"
        for forged in ({ares: {"status": "200", "body": "{}"}}, {ares: {"status": 200, "body": 5}}, {ares: "{}"}, [ares], "x"):
            with self.subTest(forged=forged):
                self.assertIn("lookup", form_scripts.run_button_script(form_running(script), 0, "", [], forged))

    def test_lookups_of_one_click_are_few(self):
        urls = [f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/0000000{digit}" for digit in range(form_scripts.MAX_LOOKUPS_PER_RUN + 1)]
        script = "".join(f"var p = new SendParam(); p.method = SM_GET; p.url = '{url}'; GetDocument().SendData(new Variant(VarStr, ''), p);" for url in urls)
        result = form_scripts.run_button_script(form_running(script), 0, "", [], {url: {"status": 200, "body": "{}"} for url in urls})
        self.assertEqual(result, {"refused": "SendData"})

    def test_loading_an_empty_part_restores_it_as_published(self):
        doc = form_running("var e = GetDocument().GetElement(); e.SetDataTo('b', 'vyplneno');" + load_part("<sekce></sekce>", "/d:root/d:sekce"))
        result = click(doc)
        self.assertNotIn("refused", result)
        self.assertFalse(doc.get_value("b"))
        self.assertIn("sekce", result["sections"])

    def test_only_an_empty_part_of_the_same_name_loads(self):
        for content, path in (("<sekce><b>cizi</b></sekce>", "/d:root/d:sekce"), ("<sekce b='cizi'/>", "/d:root/d:sekce"),
                              ("<jina></jina>", "/d:root/d:sekce"), ("cizi", "/d:root/d:sekce"), ("<!DOCTYPE x><sekce/>", "/d:root/d:sekce"),
                              ("<sekce></sekce>", "/d:root/d:neni"), ("<sekce></sekce>", "/d:root/d:sekce/text()"), ("<sekce></sekce>", "(((")):
            with self.subTest(content=content, path=path):
                doc = form_running(WRITE + load_part(content, path))
                self.assertIn("refused", click(doc))
                self.assertEqual(value_of_a(doc), "puvodni")

    def test_an_empty_part_does_not_clear_several_copies_at_once(self):
        doc = form_running("var e = GetDocument().GetElement(); e.InsertSection('//*[@form602id=\"s\"]');" + WRITE + load_part("<sekce/>", "/d:root/d:sekce"))
        self.assertIn("refused", click(doc))
        self.assertEqual(value_of_a(doc), "puvodni")

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
        shell = ui_source("shell.js")
        self.assertIn("const plainTextOf = markup => new DOMParser()", shell)
        callback = shell[shell.index("window.form_script_callback"):shell.index("const icons = {")]
        self.assertNotIn("innerHTML", callback)

    def test_opening_the_form_runs_no_script(self):
        doc = form_running("GetDocument().SendData(1);", library="kept_at_open = 1;")
        self.assertEqual(value_of_a(doc), "puvodni")
        self.assertNotIn("kept_at_open", doc.script_interpreter().globals.vars)

    def test_script_reports_a_change_the_page_cannot_show(self):
        form = FORM_WITH_SCRIPT.replace("<d:sekce><d:b/></d:sekce>", "<d:sekce><d:b/></d:sekce><d:sekce><d:b/></d:sekce>")
        script = "{ GetDocument().GetElement().SetDataTo('b', 'druha'); }"
        button = BUTTON_SCRIPT_MARK.replace(">", f" form602btnclick={quoteattr(script)}>")
        doc = engine.open_document((form % "").replace(BUTTON_SCRIPT_MARK, button).encode())
        result = form_scripts.run_button_script(doc, 0, "block---sekce-rep-1", [])
        self.assertEqual(result["values"], {})
        self.assertTrue(result["changed"])
        self.assertFalse(form_scripts.run_button_script(doc, 0, "block---sekce-rep-1", [])["changed"])

    def test_value_only_loaded_by_the_page_runs_no_change_script(self):
        form = (FORM_WITH_SCRIPT % "").replace(BUTTON_SCRIPT_MARK, BUTTON_SCRIPT_MARK.replace(">", " form602onchange=\"{ GetDocument().GetElement().SetDataTo('b', 'spusteno'); }\">"))
        doc = engine.open_document(form.encode())
        engine.handle(doc, "store_val", {"field": "a", "value": "puvodni", "input_id": "x", "repe": ""})
        self.assertNotEqual(doc.get_value("b"), "spusteno")
        engine.handle(doc, "store_val", {"field": "a", "value": "nove", "input_id": "x", "repe": ""})
        self.assertEqual(doc.get_value("b"), "spusteno")


class HostileFormStylesheets(unittest.TestCase):
    """The stylesheets a form runs with XSLCheck and GetFormData, and the JScript they carry."""

    def check(self, *stylesheets):
        doc = engine.open_document(form_with_stylesheets("GetDocument().GetElement().SetDataTo('a', 'zmeneno'); GetDocument().XSLCheck('Zla');",
                                                         stylesheets, "puvodni"))
        result = form_scripts.run_button_script(doc, 0, "", [])
        self.assertEqual(doc.get_value("a"), "puvodni")
        return result

    def test_stylesheet_reads_no_file_and_no_address(self):
        for body in ("<x><xsl:value-of select=\"document('file:///etc/passwd')\"/></x>",
                     "<x><xsl:value-of select=\"document('https://example.org/')\"/></x>"):
            with self.subTest(body):
                self.assertIn("refused", self.check(stylesheet("Zla", body)))

    def test_stylesheet_loads_no_other_stylesheet(self):
        template = stylesheet("Zla", "<control/>").replace("<msxsl:script", '<xsl:import href="file:///etc/x.xsl"/><msxsl:script')
        self.assertIn("refused", self.check(template))

    def test_stylesheet_writes_no_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "zapsano"
            template = stylesheet("Zla", f'<x><exsl:document href="{target.as_uri()}">x</exsl:document></x>').replace(
                'exclude-result-prefixes="', 'xmlns:exsl="http://exslt.org/common" extension-element-prefixes="exsl" exclude-result-prefixes="')
            self.assertIn("refused", self.check(template))
            self.assertFalse(target.exists())

    def test_its_functions_reach_nothing_of_the_form_or_the_host(self):
        for reach in ("GetDocument()", "MessageBox('x')", "SetNamedParam('a', 'b')", "eval('1')", "require('os')"):
            with self.subTest(reach):
                self.assertIn("refused", self.check(stylesheet("Zla", "<x><xsl:value-of select='js:f()'/></x>", f"function f() {{ return {reach}; }}")))

    def test_endless_function_stops(self):
        self.assertIn("refused", self.check(stylesheet("Zla", "<x><xsl:value-of select='js:f()'/></x>", "function f() { while (true) {} }")))
        self.assertIn("refused", self.check(stylesheet("Zla", "<x><xsl:value-of select='js:f(1)'/></x>", "function f(n) { return f(n + 1); }")))

    def test_calls_of_one_check_share_one_budget(self):
        busy = "function f() { for (var i = 0; i < 300000; i++) {} return 1; }"
        self.assertIn("refused", self.check(stylesheet("Zla", "<x><xsl:for-each select='(//node())[position() &lt; 50]'><xsl:value-of select='js:f()'/></xsl:for-each>"
                                                             "<xsl:value-of select='js:f()'/><xsl:value-of select='js:f()'/><xsl:value-of select='js:f()'/></x>", busy)))

    def test_a_loop_of_checks_stops(self):
        busy = "function f() { for (var i = 0; i < 300000; i++) {} return 1; }"
        doc = engine.open_document(form_with_stylesheets("GetDocument().GetElement().SetDataTo('a', 'zmeneno'); while (true) GetDocument().XSLCheck('Zla');",
                                                         [stylesheet("Zla", "<control><x><xsl:value-of select='js:f()'/></x></control>", busy)], "puvodni"))
        self.assertIn("refused", form_scripts.run_button_script(doc, 0, "", []))
        self.assertEqual(doc.get_value("a"), "puvodni")
        cheap = engine.open_document(form_with_stylesheets("while (true) GetDocument().XSLCheck('Zla');", [stylesheet("Zla", "<control/>")]))
        self.assertIn("refused", form_scripts.run_button_script(cheap, 0, "", []))

    def test_functions_in_other_languages_do_not_run(self):
        self.assertIn("refused", self.check(stylesheet("Zla", "<x><xsl:value-of select='js:f()'/></x>", "public string f() { return \"x\"; }", language="C#")))


class LocalServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        app.FORMS["cuzk-test"] = ROOT / "samples/cuzk/form.zfo"
        app.FORMS["isds-test"] = ISDS_SAMPLES / "prijata.zfo"
        app.FORMS["isds-other-test"] = ISDS_SAMPLES / "dorucenka.zfo"
        cls.folder = tempfile.TemporaryDirectory()
        signed_xml = Path(cls.folder.name) / "p7m.zfo"
        signed_xml.write_bytes(isds_sample_tool().build_message(xml=b"<x/>"))
        app.FORMS["p7m-test"] = signed_xml
        garbage = Path(cls.folder.name) / "garbage.zfo"
        garbage.write_bytes(b"\x30\x80garbage")
        app.FORMS["garbage-test"] = garbage

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for name in ("cuzk-test", "isds-test", "isds-other-test", "p7m-test", "garbage-test"):
            del app.FORMS[name]
        cls.folder.cleanup()

    def session_of(self, name):
        return page_data(self.request("GET", f"/?form={name}")[1].decode())["session"]

    def test_routing_by_content(self):
        status, page = self.request("GET", "/?form=isds-test")
        self.assertEqual(status, 200)
        self.assertIn('"kind": "received"', page.decode())
        self.assertIn("zfo-app", self.request("GET", "/?form=cuzk-test")[1].decode())
        for name in ("p7m-test", "garbage-test"):
            status, page = self.request("GET", f"/?form={name}")
            self.assertEqual((status, "Soubor nelze otevřít" in page.decode()), (422, True), name)

    def test_only_page_scripts_are_served_from_ui(self):
        for path, script in app.PAGE_SCRIPTS.items():
            self.assertEqual(self.request("GET", path), (200, script.read_bytes()), path)
        for path in ("/shell.html", "/fo2html.xsl", "/ui/shell.js", "/shell.js/../app.py", "/%2e%2e/app.py"):
            body = self.request("GET", path)[1]
            for source in (b"__PAGE_DATA__", b"xsl:stylesheet", b"script[data-zfo-page]", b"def page_with_data"):
                self.assertNotIn(source, body, path)

    def test_attachment_route_guards(self):
        session = self.session_of("isds-test")
        status, body = self.request("GET", f"/attachment?s={session}&i=0")
        self.assertEqual((status, body), (200, b"%PDF-1.4\n% TEST priloha\n"))
        other = self.session_of("isds-other-test")
        form = self.session_of("cuzk-test")
        for path in ("/attachment?i=0", f"/attachment?s={other}&i=0", f"/attachment?s={form}&i=0",
                     f"/attachment?s={session}&i=99", f"/attachment?s={session}&i=-1", f"/attachment?s={session}&i=x",
                     f"/attachment?s={session}&i=%C2%B2", f"/attachment?s={session}&i=%D9%A3",
                     f"/attachment?s={session}"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("GET", f"/attachment?s={session}&i=0", host="attacker.example")[0], 403)
        for path in ("/save", "/pdf-save", "/export-data", "/ipc"):
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", f"{path}?s={session}", body=b"{}")[0], 404)

    def test_attachment_is_never_shown_inline(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("GET", f"/attachment?s={self.session_of('isds-test')}&i=2", headers={"Host": f"127.0.0.1:{self.port}"})
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.getheader("Content-Type"), "application/octet-stream")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("Content-Disposition"), "attachment; filename*=UTF-8''evil.txt")

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
        session = page_data(page.decode())["session"]
        self.assertEqual(self.request("POST", f"/export-data?s={session}")[0], 200)
        self.assertEqual(self.request("POST", f"/export-data?s={session}", origin=f"http://127.0.0.1:{self.port}")[0], 200)
        self.assertEqual(self.request("POST", f"/export-data?s={session}", origin="https://attacker.example")[0], 403)

    def test_revocation_answers_only_about_signatures_shown(self):
        session = page_data(self.request("GET", "/?form=cuzk-test")[1].decode())["session"]
        known = next(iter(app.SHOWN_CERTIFICATES_BY_ID))
        self.assertEqual(self.request("POST", "/revocation", body=json.dumps([known]))[0], 404)
        status, answer = self.request("POST", f"/revocation?s={session}", body=json.dumps([known, "unknown-id", 5]))
        self.assertEqual(status, 200)
        self.assertEqual(list(json.loads(answer)), [known])
        self.assertEqual(self.request("POST", f"/revocation?s={session}", body="not json")[0], 400)
        self.assertEqual(self.request("POST", f"/revocation?s={session}", origin="https://attacker.example", body="[]")[0], 403)

    def test_lookup_goes_only_to_the_known_register(self):
        session = self.session_of("cuzk-test")
        ares = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/00006947"
        for body in (json.dumps({"url": "https://attacker.example/"}), json.dumps({"url": ares + "/x"}), json.dumps([ares]), "not json"):
            with self.subTest(body):
                self.assertEqual(self.request("POST", f"/lookup?s={session}", body=body)[0], 400)
        self.assertEqual(self.request("POST", f"/lookup?s={session}", origin="https://attacker.example", body=json.dumps({"url": ares}))[0], 403)
        self.assertEqual(self.request("POST", "/lookup", body=json.dumps({"url": ares}))[0], 404)
        status, answer = self.request("POST", f"/lookup?s={session}", body=json.dumps({"url": ares}))
        self.assertEqual((status, json.loads(answer)), (200, {"error": lookups.UNAVAILABLE}))

    def test_address_check_sends_only_what_the_form_collected(self):
        session = self.session_of("cuzk-test")
        self.assertEqual(self.request("POST", "/check-addresses", body="{}")[0], 404)
        self.assertEqual(self.request("POST", f"/check-addresses?s={session}", origin="https://attacker.example", body="{}")[0], 403)
        with mock.patch.object(ruian, "check") as check:
            status, answer = self.request("POST", f"/check-addresses?s={session}", body=json.dumps({"addresses": [{"Obec": "Cizi"}]}))
        self.assertEqual((status, json.loads(answer)), (200, {"results": []}))
        check.assert_not_called()
        address = {"Ulice": "", "CisloPopisne": "12", "CisloEvidencni": "", "CisloOrientacni": "", "Psc": "26301", "Obec": "Obořiště",
                   "CastObce": "Lhotka"}
        doc = app.SESSIONS[session]["doc"]
        doc.addresses_to_check, doc.addresses_asked = [address], {form_scripts.address_text(address)}
        with mock.patch.dict(os.environ, {"ZFO_OFFLINE": "1"}):
            status, answer = self.request("POST", f"/check-addresses?s={session}", body="{}")
        self.assertEqual((status, json.loads(answer)), (200, {"error": ruian.UNAVAILABLE}))
        self.assertEqual(doc.addresses_asked, set())  # not checked after all: asked about again next time

    def test_a_page_that_is_not_the_registers_answer_counts_as_unavailable(self):
        class Answer(io.BytesIO):
            def __init__(self, content_type):
                super().__init__(b"<html>Prihlaste se k Wi-Fi</html>")
                self.status, self.headers = 200, http.client.HTTPMessage()
                self.headers["Content-Type"] = content_type

        ares = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/00006947"
        for content_type, outcome in (("text/html", None), ("application/json; charset=utf-8", 200)):
            opener = mock.Mock(open=mock.Mock(return_value=Answer(content_type)))
            with self.subTest(content_type), mock.patch.dict(os.environ, {"ZFO_OFFLINE": ""}), mock.patch.object(public_services, "opener", return_value=opener):
                if outcome is None:
                    self.assertRaises(OSError, lookups.ask, ares)
                else:
                    self.assertEqual(lookups.ask(ares)["status"], outcome)

    def test_progress_of_an_unknown_download_is_not_found(self):
        self.assertEqual(self.request("GET", "/codelists-progress?t=neznamy")[0], 404)
        self.assertEqual(self.request("GET", "/codelists-progress?t=x", host="attacker.example")[0], 403)

    def test_lookup_follows_no_redirect(self):
        handler = public_services._NoRedirects()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://attacker.example/"))

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
        signatures = page_data(page.decode())["signatures"]
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

    def test_print_request_with_another_file_prints_nothing(self):
        form = mock.Mock(home="http://127.0.0.1:1/")
        form.window.get_current_url.return_value = form.home
        with tempfile.NamedTemporaryFile(prefix="zfo-tisk-", suffix=".pdf", dir=tempfile.gettempdir()) as print_file, \
                mock.patch.object(desktop, "print_pdf_file") as print_pdf_file:
            for path in (sys.executable, print_file.name):
                desktop.Bridge(mock.Mock(), form).message({"action": "printPdf", "path": path})
        print_pdf_file.assert_called_once_with(Path(print_file.name), form.window)


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


class HostileFormDataFiles(unittest.TestCase):
    def test_external_entity_is_not_read_into_values(self):
        with tempfile.TemporaryDirectory() as folder:
            outside_file = Path(folder) / "outside.txt"
            outside_file.write_text(OUTSIDE_FILE_TEXT, encoding="utf-8")
            hostile = (f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "{outside_file.as_uri()}">]>'
                       '<xfdf xmlns="http://ns.adobe.com/xfdf/"><fields><field name="a"><value>&e;</value></field></fields></xfdf>')
            values = pdf_form_data.read_form_data(hostile.encode())
        self.assertNotIn(OUTSIDE_FILE_TEXT, json.dumps(values))

    def test_entity_bomb_is_not_expanded(self):
        entities = '<!ENTITY a0 "xxxxxxxxxx">' + "".join(f'<!ENTITY a{i} "{f"&a{i - 1};" * 10}">' for i in range(1, 10))
        hostile = (f'<?xml version="1.0"?><!DOCTYPE x [{entities}]>'
                   '<xfdf xmlns="http://ns.adobe.com/xfdf/"><fields><field name="a"><value>&a9;</value></field></fields></xfdf>')
        try:
            values = pdf_form_data.read_form_data(hostile.encode())
        except ValueError:
            return
        self.assertLess(len(json.dumps(values)), 1000)

    def test_cycles_in_fdf_end(self):
        hostile = (b"%FDF-1.2\n1 0 obj << /FDF << /Fields [2 0 R 2 0 R] >> >> endobj\n"
                   b"2 0 obj << /T (a) /V 3 0 R /Kids [2 0 R 1 0 R] >> endobj\n3 0 obj 3 0 R endobj\n%%EOF")
        self.assertEqual(pdf_form_data.read_form_data(hostile), {})

    def test_unreadable_objects_get_a_message(self):
        for hostile in (b"%FDF-1.2\n1 0 obj << /FDF << /Fields " + b"[" * 100_000 + b" >> >> endobj",
                        b"%FDF-1.2\n1 0 obj " + b"<< /A " * 50_000 + b"endobj",
                        b"%FDF-1.2\n1 0 obj << /FDF << /Fields [ << /T (a) /V /" + b"a" * 5000 + b" >> ] >> >> endobj"):
            with self.subTest(size=len(hostile)), self.assertRaises(ValueError):
                pdf_form_data.read_form_data(hostile)

    def test_long_runs_of_digits_are_read_quickly(self):
        started = time.monotonic()
        for hostile in (b"%FDF-1.2\n" + b"9" * 200_000, b"%FDF-1.2\n" + b"9" * 100_000 + b" " + b"9" * 100_000 + b" obj"):
            with self.subTest(size=len(hostile)), self.assertRaises(ValueError):
                pdf_form_data.read_form_data(hostile)
        self.assertLess(time.monotonic() - started, 2)

    def test_fdf_with_bytes_before_the_header_is_read(self):
        data = b"\xef\xbb\xbfjunk\n" + pdf_form_data.write_fdf({"a": "x"}, "x.pdf").encode()
        self.assertEqual(pdf_form_data.read_form_data(data), {"a": "x"})

    def test_deeply_nested_fields_are_cut_off(self):
        nested = "".join(f'<field name="f{i}">' for i in range(200)) + "<value>x</value>" + "</field>" * 200
        values = pdf_form_data.read_form_data(f'<xfdf xmlns="http://ns.adobe.com/xfdf/"><fields>{nested}</fields></xfdf>'.encode())
        self.assertEqual(values, {})

    def test_routes_need_the_session_and_the_own_origin(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        app.FORMS["pdf-data-route"] = ROOT / "samples-pdf/zadost.pdf"
        self.addCleanup(app.FORMS.pop, "pdf-data-route", None)

        def request(path, body=b"", origin=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            headers = {"Host": f"127.0.0.1:{port}", **({"Origin": origin} if origin else {})}
            connection.request("POST" if body is not None else "GET", path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()

        _, page = request("/?form=pdf-data-route", body=None)
        session = page_data(page.decode())["session"]
        data = pdf_form_data.write_xfdf({"jmeno": "Jan"}, "zadost.pdf").encode()
        for path in ("/pdf-import-data", "/pdf-export-data", "/pdf-print-document"):
            with self.subTest(path=path):
                self.assertEqual(request(path, data)[0], 404)
                self.assertEqual(request(f"{path}?s={session}", data, origin="https://attacker.example")[0], 403)
        status, answer = request(f"/pdf-import-data?s={session}", data)
        self.assertEqual((status, json.loads(answer)["values"]), (200, {"jmeno": "Jan"}))
        self.assertEqual(request(f"/pdf-import-data?s={session}", b"<x")[0], 400)
        status, answer = request(f"/pdf-export-data?s={session}", json.dumps({"values": {"jmeno": "Jan"}}).encode())
        self.assertEqual(status, 200)
        self.assertIn("<value>Jan</value>", json.loads(answer)["contents"]["xfdf"])
        status, answer = request(f"/pdf-print-document?s={session}", json.dumps({"values": {"jmeno": "Jan"}}).encode())
        self.assertEqual((status, PdfReader(io.BytesIO(answer)).get_fields()["jmeno"]["/V"]), (200, "Jan"))
        self.assertEqual(request(f"/pdf-print-document?s={session}", b"{")[0], 400)


class ContainerAttacks(unittest.TestCase):
    FILES = {"smlouva.pdf": b"%PDF-1.7 smlouva"}
    SIGNATURE = f"{{{asic_samples.DS}}}"
    XADES = f"{{{asic_samples.XADES}}}"

    def container(self, mutate=None, files=None, **options):
        files = files or self.FILES
        signature = asic_samples.xades_signature(files, mutate=mutate, **options)
        return asic_samples.asic_e(files, {"signatures.xml": asic_samples.signatures_xml(signature)})

    def only_signature(self, data):
        description, _ = asic_verify.describe(data)
        [signature] = description["signatures"]
        return signature, description

    def assertRefused(self, mutate=None, reason=None, **options):
        # Every sample is signed over what it contains, so only the attacked property can break it; the control
        # sample without the attack proves that.
        control, _ = self.only_signature(self.container(**options))
        self.assertTrue(control["intact"], control["broken_reason"])
        signature, description = self.only_signature(self.container(mutate, **options))
        self.assertFalse(signature["intact"])
        self.assertEqual(signature["broken_reason"], reason)
        return signature, description

    def zip_of(self, entries, *, method=zipfile.ZIP_DEFLATED):
        buffer = io.BytesIO()
        with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w") as archive:
            warnings.simplefilter("ignore")
            for name, data in entries:
                archive.writestr(name, data, compress_type=method)
        return buffer.getvalue()

    def test_wrapped_signed_properties_are_refused(self):
        def mutate(signature):
            qualifying = signature.find(f".//{self.XADES}QualifyingProperties")
            original = qualifying.find(f"{self.XADES}SignedProperties")
            decoy = copy.deepcopy(original)
            decoy.set("Id", "decoy")
            original.find(f".//{self.XADES}SigningTime").text = "2020-01-01T00:00:00Z"
            etree.SubElement(signature, asic_samples._ds("Object")).append(decoy)
            signature.find(f"{self.SIGNATURE}SignedInfo/{self.SIGNATURE}Reference[@Type='{asic_samples.SIGNED_PROPERTIES_TYPE}']").set(
                "URI", "#decoy")
        self.assertRefused(mutate, xades_verify.WRAPPED)

    def test_second_object_with_qualifying_properties_is_refused(self):
        def mutate(signature):
            second = copy.deepcopy(signature.find(f"{self.SIGNATURE}Object"))
            second.find(f".//{self.XADES}SignedProperties").set("Id", "S1-other-signed-properties")
            second.find(f".//{self.XADES}SigningTime").text = "2020-01-01T00:00:00Z"
            signature.append(second)
        self.assertRefused(mutate, xades_verify.WRAPPED)

    def test_qualifying_properties_for_another_signature_are_refused(self):
        def mutate(signature):
            signature.find(f".//{self.XADES}QualifyingProperties").set("Target", "#S2")
        self.assertRefused(mutate, xades_verify.WRAPPED)

    def test_second_signed_properties_reference_is_refused(self):
        def mutate(signature):
            asic_samples._signed_properties_reference(signature, signature.find(f"{self.SIGNATURE}SignedInfo"), asic_samples.SHA256)
        self.assertRefused(mutate, xades_verify.WRAPPED)

    def test_signature_borrowing_signed_properties_of_another_is_refused(self):
        first = asic_samples.xades_signature(self.FILES)

        def borrow(signature):
            # The first signature sits next to it while the digests are computed, so the borrowed reference is genuine.
            signature.getparent().append(copy.deepcopy(first))
            signature.find(f"{self.SIGNATURE}SignedInfo/{self.SIGNATURE}Reference[@Type='{asic_samples.SIGNED_PROPERTIES_TYPE}']").set(
                "URI", "#S1-signed-properties")

        def signatures(mutate):
            second = asic_samples.xades_signature(self.FILES, signature_id="S2", mutate=mutate)
            data = asic_samples.asic_e(self.FILES, {"signatures.xml": asic_samples.signatures_xml(copy.deepcopy(first), second)})
            return asic_verify.describe(data)[0]["signatures"]
        self.assertEqual([signature["intact"] for signature in signatures(None)], [True, True])
        own, borrowing = signatures(borrow)
        self.assertTrue(own["intact"], own["broken_reason"])
        self.assertEqual((borrowing["intact"], borrowing["broken_reason"]), (False, xades_verify.WRAPPED))

    def test_internal_reference_does_not_sign_a_file_named_like_its_id(self):
        files = {"a.pdf": b"%PDF-1.7 a"}
        for manifest in (False, True):
            with self.subTest(manifest=manifest):
                def mutate(signature, manifest=manifest):
                    etree.SubElement(signature, asic_samples._ds("Object"), Id="obj").text = "podepsaný objekt"
                    parent = signature.find(f"{self.SIGNATURE}Object/{self.SIGNATURE}Manifest") if manifest else \
                        signature.find(f"{self.SIGNATURE}SignedInfo")
                    asic_samples._add_reference(parent, "#obj", asic_samples.SHA256, [asic_samples.C14N10])
                signature = asic_samples.xades_signature(files, manifest=manifest, mutate=mutate)
                data = asic_samples.asic_e({**files, "#obj": b"%PDF-1.7 podvrh"}, {"signatures.xml": asic_samples.signatures_xml(signature)})
                result, description = self.only_signature(data)
                self.assertTrue(result["intact"], result["broken_reason"])
                self.assertEqual({file["name"]: file["state"] for file in description["files"]}, {"a.pdf": "signed", "#obj": "unsigned"})
                self.assertEqual(result["files"], ["a.pdf"])

    def test_duplicate_id_is_refused(self):
        def mutate(signature):
            etree.SubElement(signature, asic_samples._ds("Object"), Id="S1-signed-properties")
        self.assertRefused(mutate, xades_verify.WRAPPED)

    def test_changed_file_in_container_is_refused(self):
        signature = asic_samples.xades_signature(self.FILES)
        data = asic_samples.asic_e({"smlouva.pdf": b"%PDF-1.7 podvrh"}, {"signatures.xml": asic_samples.signatures_xml(signature)})
        result, description = self.only_signature(data)
        self.assertFalse(result["intact"])
        self.assertEqual((result["broken_reason"], result["content_changed"]), (xades_verify.CONTENT_CHANGED, True))
        self.assertEqual(description["files"][0]["state"], "changed")

    def test_unknown_transform_is_refused(self):
        for uri in ("http://www.w3.org/TR/1999/REC-xpath-19991116", "http://www.w3.org/TR/1999/REC-xslt-19991116"):
            with self.subTest(uri=uri):
                def mutate(signature, uri=uri):
                    reference = signature.find(f"{self.SIGNATURE}SignedInfo/{self.SIGNATURE}Reference")
                    transforms = etree.Element(asic_samples._ds("Transforms"))
                    etree.SubElement(transforms, asic_samples._ds("Transform"), Algorithm=uri)
                    reference.insert(0, transforms)
                self.assertRefused(mutate, xades_verify.UNKNOWN_TRANSFORM)

    def test_unknown_signature_algorithm_is_refused(self):
        signature, _ = self.only_signature(self.container(signature_method=asic_samples.RSA_SHA1))
        self.assertFalse(signature["intact"])
        self.assertEqual(signature["broken_reason"], xades_verify.UNKNOWN_SIGNATURE_ALGORITHM)

    def test_forged_signing_certificate_digest_is_refused(self):
        key, user = asic_samples.test_identity()
        ca = x509.load_pem_x509_certificate((ROOT / "pki/ca.crt").read_bytes())

        def mutate(signature):
            digest = signature.find(f".//{self.XADES}SigningCertificateV2//{self.SIGNATURE}DigestValue")
            digest.text = base64.b64encode(hashlib.sha256(user.public_bytes(serialization.Encoding.DER)).digest()).decode()
        signature, _ = self.only_signature(self.container(mutate, key=key, cert=ca))
        self.assertFalse(signature["intact"])
        self.assertEqual(signature["broken_reason"], xades_verify.CERTIFICATE_MISMATCH)
        control, _ = self.only_signature(self.container(key=key, cert=user))
        self.assertTrue(control["intact"], control["broken_reason"])

    def test_external_entity_is_not_read(self):
        outside = Path(tempfile.mkdtemp()) / "outside.txt"
        outside.write_text(OUTSIDE_FILE_TEXT)
        self.addCleanup(outside.unlink)
        for system in ("file:///etc/passwd", outside.as_uri()):
            with self.subTest(system=system):
                xml = f'<?xml version="1.0"?><!DOCTYPE s [<!ENTITY x SYSTEM "{system}">]><s>&x;</s>'.encode()
                with self.assertRaises(xades_verify.XadesError) as raised:
                    asic_verify.describe(xml)
                self.assertNotIn(OUTSIDE_FILE_TEXT, str(raised.exception))

    def test_entity_expansion_is_refused(self):
        entities = '<!ENTITY a0 "lol">' + "".join(f'<!ENTITY a{i} "&a{i - 1};&a{i - 1};&a{i - 1};&a{i - 1};&a{i - 1};&a{i - 1};&a{i - 1};&a{i - 1};&a{i - 1};&a{i - 1};">'
                                                  for i in range(1, 10))
        xml = f'<?xml version="1.0"?><!DOCTYPE s [{entities}]><s>&a9;</s>'.encode()
        started = time.monotonic()
        with self.assertRaises(xades_verify.XadesError):
            asic_verify.describe(xml)
        self.assertLess(time.monotonic() - started, 1)

    def test_reference_escaping_the_container_is_refused(self):
        for uri in ("../x", "file:///etc/passwd", "http://example.com/x", "/META-INF/signatures.xml"):
            with self.subTest(uri=uri):
                def mutate(signature, uri=uri):
                    signature.find(f"{self.SIGNATURE}SignedInfo/{self.SIGNATURE}Reference").set("URI", uri)
                _, description = self.assertRefused(mutate, xades_verify.BAD_REFERENCE)
                self.assertEqual(description["files"][0]["state"], "unsigned")

    def test_zip_bomb_is_refused(self):
        bomb = self.zip_of([("bomb.bin", bytes(101 * 1024 * 1024)), ("META-INF/signatures.xml", b"<x/>")])
        with self.assertRaises(asic_verify.ContainerError):
            asic_verify.describe(bomb)

    def test_duplicate_zip_names_are_refused(self):
        data = self.zip_of([("smlouva.pdf", b"a"), ("smlouva.pdf", b"b"), ("META-INF/signatures.xml", b"<x/>")])
        with self.assertRaises(asic_verify.ContainerError):
            asic_verify.describe(data)

    def test_encrypted_entry_is_refused(self):
        data = bytearray(self.zip_of([("smlouva.pdf", b"zasifrovano"), ("META-INF/signatures.xml", b"<x/>")]))
        data[6] |= 1
        data[data.index(b"PK\x01\x02") + 8] |= 1
        with self.assertRaises(asic_verify.ContainerError):
            asic_verify.describe(bytes(data))

    def test_too_many_entries_are_refused(self):
        entries = [(f"f{i}.txt", b"x") for i in range(1000)] + [("META-INF/signatures.xml", b"<x/>")]
        self.assertEqual(len(entries), 1001)
        with self.assertRaises(asic_verify.ContainerError):
            asic_verify.describe(self.zip_of(entries))
