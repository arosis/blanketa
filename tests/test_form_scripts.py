"""602 JavaScript in forms: the scripts offices publish, zfoengine/js602.py, and the same expressions in Node.js."""
import json
import os
import re
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

from lxml import etree
from support import (
    NS,
    ROOT,
    form_with_stylesheets,
    needs_node,
    run_in_node,
    stylesheet,
    ui_source,
)

import app
import zfo_verify
from zfoengine import api as engine
from zfoengine import form_scripts, js602

FORM_WITH_ATTRIBUTE = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:e a=""/></d:root></fm:instance><fm:bind Id="k" nodeset="/d:root"><fm:bind Id="e" nodeset="d:e">
<fm:bind Id="a" nodeset="@a" calculate='%s'/></fm:bind></fm:bind></fm:model></fm:formparams></fo:root></dsig:Object></dsig:Signature>"""

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

    def test_a_stored_value_leaves_the_status_line_alone(self):
        answers = [*self.fill("jmeno", "Jana"),
                   *engine.handle(self.doc, "perform_click", {"command": "SendAppEvent(setdatavalue,jmeno,,Eva)"}),
                   *engine.handle(self.doc, "perform_click", {"command": "SendAppEvent(setevaldata,jmeno,,'Petr')"})]
        self.assertEqual([answer for answer in answers if answer["type"] == "gtk"], [])
        self.assertEqual(self.value("jmeno"), "Petr")

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

    def test_empty_expression_answers_nothing_and_the_script_goes_on(self):
        form = FORM_WITH_ATTRIBUTE % '{ Result("dál" + GetDocument().GetElement().DataXpath("") + GetDocument().GetElement().DataXpath("  ")); }'
        doc = engine.open_document(form.encode())
        engine.handle(doc, "recalc_form", "")
        self.assertEqual(doc._get_data_root().getroot()[0].get("a"), "dál")
        self.assertFalse(doc.script_problems)

    def test_result_that_form_filler_refuses_changes_nothing_and_is_reported(self):
        doc = engine.open_document((FORM_WITH_ATTRIBUTE % '{ Result(1); }').encode())
        engine.handle(doc, "recalc_form", "")
        self.assertEqual(doc._get_data_root().getroot()[0].get("a"), "")
        self.assertEqual(doc.script_problems, {"chyba ve skriptu formuláře"})

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


REQUIRED_A = stylesheet("Kontrola", """<control><xsl:if test="js:isBlank(string(/d:root/d:a))"><item><text code="ID1">F001: Položka
 je povinná.</text><focus>/d:root[1]/d:a[1]</focus></item></xsl:if><item><text>F900: Formulář jako celek.</text><focus>/d:root[1]/d:neni[1]</focus></item>
</control>""", "function isBlank(value) { return value.replace(/ /g, '') == ''; }")
NO_FAULTS = stylesheet("Bez", "<control/>")
ADDRESS_PATHS = stylesheet("Seznam", "<seznam><xpath>/d:root/d:a</xpath></seznam>")


FORM_WITH_SYSTEM_BIND = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:seznam/><d:email>ulozeny@example.cz</d:email></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="seznam" nodeset="d:seznam" depends="system" calculate="'PrijemcePodani'"/>
<fm:bind Id="email" nodeset="d:email" depends="seznam" calculate="concat(/d:root/d:seznam, '@example.cz')"/></fm:bind>
</fm:model></fm:formparams></fo:root></dsig:Object></dsig:Signature>"""


class SystemBinds(unittest.TestCase):
    """Binds depending on "system" are calculated from the form's own settings, as Form Filler does when a form opens."""
    def setUp(self):
        self.doc = engine.open_document(FORM_WITH_SYSTEM_BIND.encode())

    def test_opening_calculates_them_and_keeps_what_depends_on_them(self):
        self.assertEqual(self.doc.get_value("seznam"), "PrijemcePodani")
        self.assertEqual(self.doc.get_value("email"), "ulozeny@example.cz")

    def test_a_published_form_has_nothing_stored_so_what_depends_on_them_is_calculated_once_its_lists_are_there(self):
        engine.calculate_what_a_published_form_derives_from_system_binds(self.doc)
        self.assertEqual(self.doc.get_value("email"), "PrijemcePodani@example.cz")

    def test_a_filled_form_keeps_what_was_stored_with_it(self):
        filled = FORM_WITH_SYSTEM_BIND.replace("</dsig:Object></dsig:Signature>", "</dsig:Object><dsig:Object>"
                                               '<d:root xmlns:d="http://software602.cz/sample"><d:seznam>PrijemcePodani</d:seznam>'
                                               "<d:email>vyplneny@example.cz</d:email></d:root></dsig:Object></dsig:Signature>")
        doc = engine.open_document(filled.encode())
        engine.calculate_what_a_published_form_derives_from_system_binds(doc)
        self.assertEqual(doc.get_value("email"), "vyplneny@example.cz")

    def test_binds_that_depend_on_each_other_do_not_keep_a_published_form_from_opening(self):
        circular = FORM_WITH_SYSTEM_BIND.replace("<d:seznam/>", "<d:seznam/><d:kruh/>").replace('depends="seznam"', 'depends="seznam|kruh"').replace(
            "</fm:bind>\n</fm:model>", '<fm:bind Id="kruh" nodeset="d:kruh" depends="email" calculate="/d:root/d:email"/></fm:bind>\n</fm:model>')
        doc = engine.open_document(circular.encode())
        engine.calculate_what_a_published_form_derives_from_system_binds(doc)
        self.assertEqual(doc.get_value("seznam"), "PrijemcePodani")

    def test_recalculation_calculates_them_and_what_depends_on_them(self):
        engine.handle(self.doc, "recalc_form", "")
        self.assertEqual(self.doc.get_value("email"), "PrijemcePodani@example.cz")


class FormStylesheetChecks(unittest.TestCase):
    """Forms of MPSV check their data with their own stylesheets, document.XSLCheck() in their scripts."""

    def click(self, script, value="", stylesheets=(REQUIRED_A, NO_FAULTS, ADDRESS_PATHS)):
        doc = engine.open_document(form_with_stylesheets(script, stylesheets, value))
        return form_scripts.run_button_script(doc, 0, "", [])

    def test_faults_come_with_their_field_and_their_words(self):
        result = self.click("if (!GetDocument().XSLCheck('Kontrola')) MessageBox('chyby');")
        self.assertEqual(result["problems"], [{"field": "a", "index": 0, "kind": "form", "message": "F001: Položka je povinná."},
                                              {"field": None, "index": 0, "kind": "form", "message": "F900: Formulář jako celek."}])
        self.assertEqual(result["messages"], [{"text": "chyby", "title": ""}])

    def test_check_without_faults_passes(self):
        result = self.click("if (GetDocument().XSLCheck('Bez')) MessageBox('v poradku');", value="vyplneno")
        self.assertEqual((result["problems"], result["messages"]), ([], [{"text": "v poradku", "title": ""}]))

    def test_warnings_are_shown_only_when_nothing_is_wrong_and_do_not_fail_the_check(self):
        warned = stylesheet("Varuje", """<control><warning><text>Příjem je
 neobvykle vysoký.</text></warning><warning><text>Zkontrolujte datum.</text></warning><warning><text/></warning></control>""")
        result = self.click("if (GetDocument().XSLCheck('Varuje')) MessageBox('v poradku');", stylesheets=(warned,))
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["messages"], [{"text": "Příjem je neobvykle vysoký.\nZkontrolujte datum.", "title": "Varování"},
                                              {"text": "v poradku", "title": ""}])
        faulty = stylesheet("Chyba", "<control><item><text>F001</text></item><warning><text>Zkontrolujte datum.</text></warning></control>")
        result = self.click("GetDocument().XSLCheck('Chyba');", stylesheets=(faulty,))
        self.assertEqual((len(result["problems"]), result["messages"]), (1, []))

    def test_noclean_adds_to_the_earlier_check_and_a_plain_check_starts_anew(self):
        twice = self.click("var d = GetDocument(); d.XSLCheck('Kontrola'); d.XSLCheck('Kontrola', 'noclean');")
        self.assertEqual(len(twice["problems"]), 4)
        cleaned = self.click("var d = GetDocument(); d.XSLCheck('Kontrola'); d.XSLCheck('Bez');")
        self.assertEqual(cleaned["problems"], [])

    def test_faults_in_a_copy_added_between_checks_find_their_field(self):
        copy_fault = stylesheet("Kopie", "<control><item><text>F002</text><focus>/d:root[1]/d:sekce[2]/d:c[1]</focus></item></control>")
        result = self.click("var d = GetDocument(); d.XSLCheck('Kontrola'); d.GetElement().InsertSection('//*[@form602id=\"s\"]');"
                            "d.XSLCheck('Kopie', 'noclean');", stylesheets=(REQUIRED_A, copy_fault))
        self.assertEqual(result["problems"][-1], {"field": "c", "index": 1, "kind": "form", "message": "F002"})

    def test_form_data_of_a_stylesheet_reaches_the_script(self):
        result = self.click("var p = new DataParam(); p.xslName = 'Seznam'; var r = GetDocument().GetFormData(p);"
                            "GetDocument().GetElement().SetDataTo('a', /<xpath>([^<]*)<\\/xpath>/.exec(r.data)[1]);")
        self.assertEqual(result["values"], {"a": "/d:root/d:a"})

    def test_address_lists_of_both_generations_are_empty_and_left_to_blanketa(self):
        for name in ("XsltGetAdresy", "Validace_AdresaWS_XpathAdres"):
            with self.subTest(name):
                result = self.click(f"var p = new DataParam(); p.xslName = '{name}';"
                                    "if (GetDocument().GetFormData(p).data == '') GetDocument().GetElement().SetDataTo('a', 'prazdny');",
                                    stylesheets=(stylesheet(name, "<x><xpath>/d:root/d:a</xpath></x>"),))
                self.assertEqual(result["values"], {"a": "prazdny"})
                self.assertEqual((result["notices"], result["addresses"]), ([], []))

    def test_a_check_that_answers_otherwise_is_refused_instead_of_passing(self):
        for body in ("", "<x/>", '<control xmlns="urn:jiny"/>'):
            with self.subTest(body):
                self.assertEqual(self.click("GetDocument().XSLCheck('Jina');", stylesheets=(stylesheet("Jina", body),))["refused"], "XSLCheck")

    def test_unknown_stylesheet_is_refused(self):
        self.assertEqual(self.click("var p = new DataParam(); p.xslName = 'Neni'; GetDocument().GetFormData(p);")["refused"], "GetFormData")
        self.assertIn("refused", self.click("GetDocument().XSLCheck('Neni');"))


EXPORT = stylesheet("XmlExport", "<d:Data><xsl:value-of select='/d:root/d:a'/></d:Data>")
SAVE_EXPORT = "var p = new DataParam(); p.requiredFormat = VarFXML; p.xslName = 'XmlExport'; GetDocument().SaveData(GetDocument().GetFormData(p), %s);"

PRINT_COPY = ("var doc = GetDocument(); var o = new DataParam(); o.requiredFormat = VarFFo; o.flags = GFD_ORIGINAL;"
              "var aux = doc.OpenAux(doc.GetFormData(o)); %s var d = new DataParam(); d.xslName = 'XmlExport';"
              "aux.LoadFormData(doc.GetFormData(d)); var p = new DataParam(); p.requiredFormat = VarFPdf; var pdf = aux.GetFormData(p);")


class WindowFunctions(unittest.TestCase):
    """The buttons of MPSV forms that save the data, close the form and offer its parts (Uložit, Zavřít, Přejít na…)."""

    def click(self, script, value="Jana"):
        doc = engine.open_document(form_with_stylesheets(script, (EXPORT,), value))
        return form_scripts.run_button_script(doc, 0, "", [])

    def test_saved_data_reach_the_page_with_the_name_the_form_gives(self):
        result = self.click(SAVE_EXPORT % "'12.xml'")
        self.assertEqual(result["save"]["name"], "12.xml")
        self.assertIn("<d:Data", result["save"]["content"])
        self.assertIn(">Jana</d:Data>", result["save"]["content"])

    def test_a_name_that_is_no_plain_xml_file_name_is_refused(self):
        for name in ("'../12.xml'", "'C:\\\\x.xml'", "'data.pdf'", "''", "'.xml'"):
            with self.subTest(name):
                self.assertEqual(self.click(SAVE_EXPORT % name)["refused"], "SaveData")

    def test_one_file_per_click(self):
        self.assertEqual(self.click(SAVE_EXPORT % "'a.xml'" + SAVE_EXPORT % "'b.xml'")["refused"], "SaveData")
        self.assertEqual(self.click("GetDocument().SaveData('text', 'a.xml');")["refused"], "SaveData")

    def test_a_part_of_the_data_passes_through_a_stylesheet_into_the_file(self):
        rename = stylesheet("Prejmenuj", "<ulozeno><xsl:value-of select='/d:sekce/d:c | /d:root/d:a'/></ulozeno>")
        script = ("var p = new DataParam(); p.dataPartXPath = '%s'; p.requiredFormat = VarFXML; var out = GetDocument().GetFormData(p);"
                  "GetDocument().TransformData(out, 'Prejmenuj'); GetDocument().SaveData(out, 'ulozeno.xml');")
        for path, saved in (("", "<ulozeno>Jana</ulozeno>"), ("/d:root/d:sekce", "<ulozeno/>")):
            with self.subTest(path):
                doc = engine.open_document(form_with_stylesheets(script % path, (rename,), "Jana"))
                self.assertIn(saved, form_scripts.run_button_script(doc, 0, "", [])["save"]["content"])

    def test_data_a_script_transforms_never_read_a_file(self):
        copy = stylesheet("Kopie", "<xsl:copy-of select='/'/>")
        script = ("var v = new Variant(); v.data = '<!DOCTYPE x [<!ENTITY e SYSTEM \\\"%s\\\">]><x>&e;</x>';"
                  " GetDocument().TransformData(v, 'Kopie'); GetDocument().SaveData(v, 'x.xml');") % (ROOT / "VERSION").as_uri()
        doc = engine.open_document(form_with_stylesheets(script, (copy,)))
        saved = form_scripts.run_button_script(doc, 0, "", [])["save"]["content"]
        self.assertIn("<x>&e;</x>", saved)
        self.assertNotIn((ROOT / "VERSION").read_text().strip(), saved)

    def test_a_missing_part_of_the_data_stops_the_script(self):
        self.assertEqual(self.click("var p = new DataParam(); p.dataPartXPath = '/d:root/d:neni'; GetDocument().GetFormData(p);")["refused"],
                         "GetFormData")

    def test_the_form_closes_its_window_and_offers_its_parts(self):
        closing = self.click("if (MessageBox('Zavřít?', '', MB_YESNO) == IDYES) GetDocument().CloseApp(CLOSE_IGNORECHANGES);")
        self.assertIn("question", closing)
        doc = engine.open_document(form_with_stylesheets(
            "if (MessageBox('Zavřít?', '', MB_YESNO) == IDYES) GetDocument().CloseApp(CLOSE_IGNORECHANGES);", (EXPORT,)))
        self.assertTrue(form_scripts.run_button_script(doc, 0, "", [True])["close"])
        self.assertNotIn("close", form_scripts.run_button_script(doc, 0, "", [False]))
        self.assertTrue(self.click("GetDocument().NavigationDlg(0, 0);")["navigate"])

    def test_showing_the_filled_form_as_pdf_prints_the_open_form(self):
        self.assertTrue(self.click(PRINT_COPY % "" + "doc.ShowData(pdf);")["print"])
        self.assertNotIn("print", self.click("GetDocument().NavigationDlg(0, 0);"))

    def test_saving_the_filled_form_as_pdf_prints_it_and_says_where_the_pdf_comes_from(self):
        result = self.click(PRINT_COPY % "" + "doc.SaveData(pdf, 'Zadost_nepodepsany.pdf');")
        self.assertTrue(result["print"])
        self.assertEqual(result["notices"], [form_scripts.PDF_FROM_PRINT_NOTICE])
        self.assertNotIn("save", result)

    def test_the_form_is_printed_once_whatever_the_form_asks_about_further_pages(self):
        result = self.click(PRINT_COPY % "" + "doc.ShowData(pdf); if (MessageBox('Další stránka?', '', MB_YESNO) == IDYES)"
                            " { doc.ShowData(pdf); doc.GetElement().SetDataTo('a', 'další'); }")
        self.assertTrue(result["print"])
        self.assertEqual(result["values"], {})

    def test_a_changed_field_prints_nothing(self):
        script = quoteattr("{" + PRINT_COPY % "" + "doc.ShowData(pdf);}").encode()
        form = form_with_stylesheets("", (EXPORT,)).replace(b'form602bind="a"/>', b'form602bind="a" form602onchange=' + script + b"/>")
        result = form_scripts.run_field_change_script(engine.open_document(form), "a", "", [])
        self.assertEqual(result["refused"], "tisk bez stisku tlačítka")

    def test_only_the_filled_copy_of_the_form_itself_is_printed(self):
        for script, stop in ((PRINT_COPY % "aux.CleanForm();" + "doc.ShowData(pdf);", "tisk prázdného formuláře"),
                             (PRINT_COPY.replace("aux.LoadFormData(doc.GetFormData(d));", "") % "" + "doc.ShowData(pdf);", "GetFormData"),
                             ("var v = new Variant(); v.data = '<fo/>'; GetDocument().OpenAux(v);", "document.OpenAux"),
                             ("var v = new Variant(); v.data = '%PDF'; GetDocument().ShowData(v);", "ShowData"),
                             (PRINT_COPY % "" + "doc.SaveData(pdf, 'a.xml');", "SaveData"),
                             (PRINT_COPY % "" + "doc.GetElement().SetDataTo('a', VariantToBase64(pdf));", "VariantToBase64")):
            with self.subTest(stop):
                self.assertEqual(self.click(script)["refused"], stop)

    def test_the_page_knows_the_parts_a_form_names(self):
        form = """<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format"><fo:page-sequence master-reference="p"><fo:flow flow-name="b">
<fo:block form602anchor=" A. Žadatel "><fo:block>Jméno</fo:block></fo:block><fo:block form602anchor=""/></fo:flow></fo:page-sequence></fo:root>"""
        page = etree.tostring(app.XSLT(etree.ElementTree(etree.fromstring(form)), PATH_TO_JS="'/s/'", PATH_TO_CSS="'/s/'"),
                              method="html", encoding="unicode")
        self.assertEqual(etree.HTML(page).xpath("//*[@data-anchor]/@data-anchor"), ["A. Žadatel"])

    def test_constants_have_form_fillers_values(self):
        result = self.click("GetDocument().GetElement().SetDataTo('a', [CLOSE_IGNORECHANGES, PI_BEGINPROGRESS, VarFXML, SM_GET,"
                            " SHTTP_NODLG, GFD_CUTNAMESPACES].join(','));")
        self.assertEqual(result["values"], {"a": "1,1,7,2,512,2"})


LOAD_SECTION = ("var doc = GetDocument(); var data = doc.GetElement().GetUploadedFileData();"
                "if (EvalXPath(data, \"/*[local-name()='sekce']/@pro\") == 'vzorek') doc.LoadFormDataPart(data, '/d:root/d:sekce');"
                "else MessageBox('Data nepatří k formuláři.', '');")
SAVED_SECTION = '<d:sekce xmlns:d="http://software602.cz/sample" pro="vzorek"><d:c>ze souboru</d:c></d:sekce>'


FIVE_AND_AN_EMPTY_NUMBER = SAVED_SECTION.replace("<d:c>ze souboru</d:c>", "<d:c/><d:n1>5</d:n1><d:n2/>")


def form_with_a_sum():
    form = form_with_stylesheets(LOAD_SECTION, ()).replace(b"<d:sekce><d:c/></d:sekce>", b"<d:sekce><d:c/><d:n1/><d:n2/></d:sekce><d:soucet/>")
    form = form.replace(b'<fm:bind Id="c" nodeset="d:c"/></fm:bind>',
                        b'<fm:bind Id="c" nodeset="d:c"/><fm:bind Id="n1" nodeset="d:n1"/><fm:bind Id="n2" nodeset="d:n2"/></fm:bind>'
                        b'<fm:bind Id="soucet" nodeset="d:soucet" depends="n1|n2" calculate="/d:root/d:sekce/d:n1 + /d:root/d:sekce/d:n2"/>')
    form = form.replace(b'<fo:inline form602ct="txt" form602bind="c"/></fo:block>',
                        b'<fo:inline form602ct="txt" form602bind="c"/><fo:inline form602ct="num" form602bind="n1"/>'
                        b'<fo:inline form602ct="num" form602bind="n2"/></fo:block><fo:inline form602ct="num" form602bind="soucet"/>')
    return form


class FilesTheUserChooses(unittest.TestCase):
    """The Nahrát uložená data (XML) button of MPSV forms loads a file the user chooses back into the data."""

    def click(self, uploaded_file=SAVED_SECTION, script=LOAD_SECTION, answers=()):
        doc = engine.open_document(form_with_stylesheets(script, ()))
        return doc, form_scripts.run_button_script(doc, 0, "", list(answers), None, uploaded_file)

    def test_the_chosen_file_fills_the_part_of_the_data_the_form_names(self):
        doc, result = self.click()
        self.assertEqual(result["values"], {"c": "ze souboru"})
        self.assertEqual(doc.get_value("c", "sekce-rep-0"), "ze souboru")
        self.assertTrue(result["changed"])
        self.assertIn("sections", result)

    def test_the_form_judges_the_file_before_loading_it(self):
        doc, result = self.click(SAVED_SECTION.replace("vzorek", "jiny"))
        self.assertEqual(result["messages"], [{"text": "Data nepatří k formuláři.", "title": ""}])
        self.assertFalse(result["changed"])

    def test_without_a_chosen_file_the_script_stops(self):
        for uploaded_file in (None, 5, "x" * (form_scripts.MAX_UPLOADED_CHARACTERS + 1)):
            with self.subTest(str(uploaded_file)[:5]):
                self.assertEqual(self.click(uploaded_file)[1]["refused"], "element.GetUploadedFileData")

    def test_a_file_without_a_namespace_fills_the_part_too(self):
        doc, result = self.click('<sekce pro="vzorek"><c>bez jmenného prostoru</c></sekce>')
        self.assertEqual(doc.get_value("c", "sekce-rep-0"), "bez jmenného prostoru")

    def test_a_file_that_does_not_fit_the_part_changes_nothing(self):
        for uploaded_file in ('<x:sekce xmlns:x="urn:jiny" pro="vzorek"><x:c>jiný jmenný prostor</x:c></x:sekce>',
                              '<d:sekce xmlns:d="http://software602.cz/sample" pro="vzorek"><d:c>', "není XML"):
            with self.subTest(uploaded_file[:20]):
                doc, result = self.click(uploaded_file)
                self.assertIn(result["refused"], ("document.LoadFormDataPart", "EvalXPath"))
                self.assertFalse(doc.get_value("c", "sekce-rep-0"))

    def test_a_part_missing_in_the_file_stays_as_the_form_published_it(self):
        doc, result = self.click('<d:sekce xmlns:d="http://software602.cz/sample" pro="vzorek"/>')
        self.assertNotIn("refused", result)
        doc.store_value("c", "dopsáno", "sekce-rep-0")
        self.assertEqual(doc.get_value("c", "sekce-rep-0"), "dopsáno")

    def test_an_empty_number_of_the_file_counts_as_zero_in_the_forms_sums(self):
        doc = engine.open_document(form_with_a_sum())
        form_scripts.run_button_script(doc, 0, "", [], None, FIVE_AND_AN_EMPTY_NUMBER)
        doc.store_value("n1", "6", "sekce-rep-0")
        self.assertEqual(doc.get_value("soucet"), "6")

    def test_sums_follow_the_loaded_numbers_without_any_typing(self):
        doc = engine.open_document(form_with_a_sum())
        result = form_scripts.run_button_script(doc, 0, "", [], None, FIVE_AND_AN_EMPTY_NUMBER)
        self.assertEqual(doc.get_value("soucet"), "5")
        self.assertEqual(result["values"]["soucet"], "5")

    def test_the_whole_recalculation_counts_an_empty_number_as_zero_like_typing_does(self):
        typed = engine.open_document(form_with_a_sum())
        typed.store_value("n1", "5", "sekce-rep-0")
        recalculated = engine.open_document(form_with_a_sum())
        form_scripts.run_button_script(recalculated, 0, "", [], None, FIVE_AND_AN_EMPTY_NUMBER)
        engine.handle(recalculated, "recalc_form", "")
        self.assertEqual(recalculated.get_value("soucet"), typed.get_value("soucet"))
        self.assertEqual(typed.get_value("soucet"), "5")
        self.assertFalse(recalculated.get_value("n2", "sekce-rep-0"))

    def test_a_number_the_user_deleted_counts_as_zero_like_form_filler_does(self):
        doc = engine.open_document(form_with_a_sum())
        doc.store_value("n1", "100", "sekce-rep-0")
        doc.store_value("n2", "7", "sekce-rep-0")
        doc.store_value("n2", "", "sekce-rep-0")
        self.assertEqual(doc.get_value("soucet"), "100")
        engine.handle(doc, "recalc_form", "")
        self.assertEqual(doc.get_value("soucet"), "100")
        doc.store_value("n1", "200", "sekce-rep-0")
        self.assertEqual(doc.get_value("soucet"), "200")
        self.assertFalse(doc.get_value("n2", "sekce-rep-0"))

    def test_the_chosen_file_reads_no_other_file(self):
        hostile = ('<!DOCTYPE d [<!ENTITY e SYSTEM "%s">]>' % (ROOT / "VERSION").as_uri()) + SAVED_SECTION.replace("ze souboru", "&e;")
        doc, result = self.click(hostile)
        self.assertNotIn((ROOT / "VERSION").read_text().strip(), json.dumps(result))

    def test_a_question_of_the_form_keeps_the_chosen_file(self):
        asking = "if (MessageBox('Nahrát?', '', MB_YESNO) == IDYES) {" + LOAD_SECTION + "}"
        doc, result = self.click(script=asking)
        self.assertEqual(result["question"]["retry"]["upload"], SAVED_SECTION)
        doc, result = self.click(script=asking, answers=[True])
        self.assertEqual(result["values"], {"c": "ze souboru"})

    def test_the_page_offers_a_file_only_where_the_form_asks_for_xml(self):
        form = """<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format"><fo:page-sequence master-reference="p"><fo:flow flow-name="b"><fo:block>
<fo:inline form602ct="bib" form602webaccept=".xml" blanketa-button="3">Nahrát uložená data (XML)</fo:inline>
<fo:inline form602ct="bib" form602webaccept=".pdf,.xml" blanketa-button="4">Nahrát soubor</fo:inline></fo:block></fo:flow></fo:page-sequence></fo:root>"""
        page = etree.HTML(etree.tostring(app.XSLT(etree.ElementTree(etree.fromstring(form)), PATH_TO_JS="'/s/'", PATH_TO_CSS="'/s/'"),
                                         method="html", encoding="unicode"))
        self.assertEqual(page.xpath("//button[@data-form-upload]/@data-form-button"), ["3"])
        self.assertEqual(page.xpath("//button[@data-form-upload]/@data-form-upload"), [".xml"])

    def test_no_button_of_a_form_is_printed(self):
        form = """<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format"><fo:page-sequence master-reference="p"><fo:flow flow-name="b"><fo:block>
<fo:inline form602ct="bib" form602webaccept=".xml" blanketa-button="3">Nahrát uložená data (XML)</fo:inline>
<fo:inline form602ct="btn" form602btnclick="{ Ulozit(); }" blanketa-button="4">Uložit data (XML)</fo:inline>
<fo:inline form602ct="btn" form602btnclick="checkdata()">Zkontrolovat</fo:inline></fo:block></fo:flow></fo:page-sequence></fo:root>"""
        page = etree.HTML(etree.tostring(app.XSLT(etree.ElementTree(etree.fromstring(form)), PATH_TO_JS="'/s/'", PATH_TO_CSS="'/s/'"),
                                         method="html", encoding="unicode"))
        hidden_on_print = re.search(r"\.zfo-page button\[[^{]*\{ visibility: hidden; \}", ui_source("shell.html")).group(0)
        marks = re.findall(r"button\[([\w-]+)\]", hidden_on_print)
        buttons = page.xpath("//button")
        self.assertEqual(len(buttons), 3)
        for button in buttons:
            self.assertTrue(any(mark in button.attrib for mark in marks), etree.tostring(button, encoding="unicode"))


GROUP_OF_ITEMS = '<d:b/><d:skupina><d:polozka kod=""><d:hodnota/><d:popis>vydaný</d:popis></d:polozka></d:skupina>'.encode()


class PartsAScriptMakes(unittest.TestCase):
    """Smazat údaje and Odeslat of MPSV forms load a group the script itself wrote, with as many items as it needs."""

    def data_after(self, *contents, path="/d:root/d:skupina", published=GROUP_OF_ITEMS):
        loads = "".join("var v = new Variant(); v.data = '%s'; GetDocument().LoadFormDataPart(v, '%s');" % (content, path) for content in contents)
        doc = engine.open_document(form_with_stylesheets(loads, ()).replace(b"<d:b/>", published))
        result = form_scripts.run_button_script(doc, 0, "", [])
        group = doc._get_data_root().getroot().find("d:skupina", NS)
        return result, [(item.get("kod"), item.findtext("d:hodnota", None, NS), item.findtext("d:popis", None, NS)) for item in group]

    def test_every_item_the_script_wrote_is_a_copy_of_the_published_one(self):
        result, items = self.data_after('<skupina><polozka kod="1"><hodnota>první</hodnota></polozka><polozka></polozka><polozka><popis>třetí</popis></polozka></skupina>')
        self.assertNotIn("refused", result)
        self.assertEqual(items, [("1", "první", "vydaný"), ("", "", "vydaný"), ("", "", "třetí")])

    def test_a_later_load_starts_from_the_published_part_again(self):
        result, items = self.data_after("<skupina><polozka/><polozka/><polozka/></skupina>", "<skupina><polozka><hodnota>jediná</hodnota></polozka></skupina>")
        self.assertEqual(items, [("", "jediná", "vydaný")])

    def test_a_group_has_as_many_items_as_the_script_wrote(self):
        published_twice = GROUP_OF_ITEMS.replace(b"</d:polozka>", '</d:polozka><d:polozka kod=""><d:hodnota/><d:popis>druhý</d:popis></d:polozka>'.encode())
        result, items = self.data_after("<skupina><polozka><hodnota>jediná</hodnota></polozka></skupina>", published=published_twice)
        self.assertEqual(items, [("", "jediná", "vydaný")])
        result, items = self.data_after("<skupina/>", published=published_twice)
        self.assertEqual([description for _, _, description in items], ["vydaný", "druhý"])

    def test_an_empty_element_clears_what_the_form_published_and_a_missing_one_keeps_it(self):
        result, items = self.data_after("<skupina><polozka><popis/></polozka></skupina>")
        self.assertEqual(items, [("", "", "")])
        result, items = self.data_after("<skupina><polozka><hodnota>h</hodnota></polozka></skupina>")
        self.assertEqual(items, [("", "h", "vydaný")])

    def test_nothing_the_form_does_not_have_gets_into_the_data(self):
        result, items = self.data_after('<skupina cizi="x"><polozka cizi="x"><hodnota>h</hodnota><cizi>x</cizi></polozka><cizi><polozka/></cizi></skupina>')
        self.assertEqual(items, [("", "h", "vydaný")])
        self.assertNotIn("cizi", json.dumps(result))

    def test_what_is_no_part_of_the_form_is_not_loaded(self):
        for content, path in (("<jina/>", "/d:root/d:skupina"), ('<x:skupina xmlns:x="urn:jiny"/>', "/d:root/d:skupina"), ("<!DOCTYPE x><skupina/>", "/d:root/d:skupina"),
                              ("<skupina>", "/d:root/d:skupina"), ("text", "/d:root/d:skupina"), ("<root/>", "/d:root"), ("<skupina/>", "/d:root/d:neni")):
            with self.subTest(content=content, path=path):
                self.assertEqual(self.data_after(content, path=path)[0]["refused"], "document.LoadFormDataPart")

    def test_the_items_a_script_makes_count_among_its_writes(self):
        months = "<skupina>" + "<polozka/>" * 300 + "</skupina>"
        self.assertEqual(len(self.data_after(months)[1]), 300)
        too_many = "<skupina>" + "<polozka/>" * (form_scripts.MAX_WRITES_PER_RUN + 2) + "</skupina>"
        self.assertEqual(self.data_after(too_many)[0]["refused"], "skript nedoběhl")


SHOW_HELP = ("var doc = GetDocument(); var p = new SendParam(); p.url = '%s'; p.method = %s;"
             "var response = doc.SendData(new Variant(VarStr, ' '), p);"
             "if (response && response.code == SR_OK && response.data.format != VarFUndef) doc.ShowData(response.data);"
             "else MessageBox('Nápovědu se nepodařilo načíst.', '');")
HELP_ON_THE_WEB = "https://mpsv.gov.cz/documents/20142/36042/napoveda_formulare.pdf"


class DocumentsOnTheWeb(unittest.TestCase):
    """The Nápověda button of MPSV forms shows a PDF the ministry publishes; Blanketa offers to open it in the browser."""

    def click(self, url="documents/20142/36042/napoveda_formulare.pdf", method="SM_GET", then=""):
        doc = engine.open_document(form_with_stylesheets(SHOW_HELP % (url, method) + then, ()))
        return form_scripts.run_button_script(doc, 0, "", [])

    def test_the_help_of_mpsv_forms_is_offered_at_the_ministrys_address(self):
        for url in ("documents/20142/36042/napoveda_formulare.pdf", HELP_ON_THE_WEB):
            with self.subTest(url):
                result = self.click(url)
                self.assertEqual(result["open"]["url"], HELP_ON_THE_WEB)
                self.assertIn("mpsv.gov.cz", result["open"]["question"])
                self.assertEqual(result["messages"], [])

    def test_newer_forms_mark_the_document_as_pdf_before_showing_it(self):
        marked = SHOW_HELP.replace("doc.ShowData(response.data);", "{ var data = response.data; data.format = VarFPdf; doc.ShowData(data); }")
        doc = engine.open_document(form_with_stylesheets(marked % ("documents/20142/36042/napoveda_formulare.pdf", "SM_GET"), ()))
        self.assertEqual(form_scripts.run_button_script(doc, 0, "", [])["open"]["url"], HELP_ON_THE_WEB)

    def test_no_other_address_is_offered(self):
        for url in ("https://example.com/documents/1/2/napoveda.pdf", "documents/1/2/../../o/rest/formulare/362_tisk",
                    "documents/1/2/napoveda.pdf?jmeno=Jana", "o/rest/formulare/362_tisk", "http://mpsv.gov.cz/documents/1/2/n.pdf",
                    "https://mpsv.gov.cz/documents/1/2/n.exe"):
            with self.subTest(url):
                self.assertEqual(self.click(url)["refused"], "SendData")
        self.assertEqual(self.click(method="SM_POST")["refused"], "SendData")

    def test_a_script_asks_for_no_more_documents_than_for_register_lookups(self):
        ask = "var p = new SendParam(); p.url = 'documents/1/2/n.pdf'; p.method = SM_GET; for (var i = 0; i < %d; i++) GetDocument().SendData(new Variant(), p);"
        click = lambda times: form_scripts.run_button_script(engine.open_document(form_with_stylesheets(ask % times, ())), 0, "", [])
        self.assertNotIn("refused", click(form_scripts.MAX_LOOKUPS_PER_RUN))
        self.assertEqual(click(form_scripts.MAX_LOOKUPS_PER_RUN + 1)["refused"], "SendData")

    def test_the_document_is_only_shown(self):
        for then, stop in (("doc.GetElement().SetDataTo('a', response.data.data);", "dokument z webu.data"),
                           ("doc.OpenAux(response.data);", "document.OpenAux"),
                           ("response.data.adresa = 'https://example.com';", "dokument z webu.adresa"),
                           ("doc.ShowData = function () {};", "document.ShowData"),
                           ("doc.SaveData(response.data, 'napoveda.xml');", "dokument z webu.data"),
                           ("doc.ShowData(response.data);", "ShowData")):
            with self.subTest(then):
                self.assertEqual(self.click(then=then)["refused"], stop)

    def test_a_changed_field_opens_nothing(self):
        script = quoteattr("{" + SHOW_HELP % ("documents/1/2/napoveda.pdf", "SM_GET") + "}").encode()
        form = form_with_stylesheets("", ()).replace(b'form602bind="a"/>', b'form602bind="a" form602onchange=' + script + b"/>")
        result = form_scripts.run_field_change_script(engine.open_document(form), "a", "", [])
        self.assertEqual(result["refused"], "dokument z webu bez stisku tlačítka")


ARES_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/00006947"
ASK_ARES = ("var p = new SendParam(); p.method = SM_GET; p.flags = SHTTP_NODLG | SHTTP_WEB_SERVERSENDS; p.url = '%s';"
            "var r = GetDocument().SendData(new Variant(VarStr, ' '), p);"
            "if (r && r.code == SR_OK) GetDocument().GetElement().SetDataTo('a', JSON.parse(r.data.data)?.obchodniJmeno || 'nic');")


class RegisterLookups(unittest.TestCase):
    """Forms fill a company in from ARES by its IČO; the user confirms each lookup and the server makes it."""

    def click(self, lookups=None, url=ARES_URL):
        doc = engine.open_document(form_with_stylesheets(ASK_ARES % url, (), "puvodni"))
        return doc, form_scripts.run_button_script(doc, 0, "", [], lookups)

    def test_lookup_is_asked_first_and_names_what_leaves(self):
        doc, result = self.click()
        self.assertEqual(result["lookup"]["url"], ARES_URL)
        self.assertIn("IČO 00006947 Ministerstvu financí (ares.gov.cz)", result["lookup"]["question"])
        self.assertEqual(result["lookup"]["retry"], {"button": 0, "repe": "", "answers": [], "lookups": {}})
        self.assertEqual(doc.get_value("a"), "puvodni")

    def test_answer_reaches_the_script(self):
        _, result = self.click({ARES_URL: {"status": 200, "body": '{"obchodniJmeno": "Ministerstvo financí"}'}})
        self.assertEqual(result["values"], {"a": "Ministerstvo financí"})

    def test_the_resident_register_of_jmk_forms_is_known_too(self):
        url = ARES_URL.replace("ekonomicke-subjekty/", "ekonomicke-subjekty-res/")
        self.assertEqual(self.click(url=url)[1]["lookup"]["url"], url)

    def test_unknown_subject_stops_with_words_and_keeps_the_data(self):
        doc, result = self.click({ARES_URL: {"status": 404, "body": "{}"}})
        self.assertIn("subjekt s\u00a0IČO 00006947 nezná", result["stopped"])
        self.assertEqual(doc.get_value("a"), "puvodni")
        self.assertIn("chyba 500", self.click({ARES_URL: {"status": 500, "body": ""}})[1]["stopped"])

    def test_the_closed_ares_service_is_named_as_closed(self):
        result = self.click(url="http://wwwinfo.mfcr.cz/cgi-bin/ares/darv_std.cgi?ico=00006947")[1]
        self.assertEqual(result, {"stopped": form_scripts.CLOSED_ARES_NOTICE})


class Interpreter(unittest.TestCase):
    """What matters about zfoengine/js602.py that a form cannot show."""

    def run_script(self, source, answer="", library=()):
        return js602.evaluate(js602.Interpreter(library), source, lambda expression: answer, "/d:root/d:x")

    def test_python_stays_out_of_reach(self):
        for reach in ('"".__class__', '[].__class__', 'Result.__globals__', 'GetDocument.__self__', '(function(){}).__code__',
                      '"".constructor', '({}).__proto__', 'new Date().__class__'):
            with self.subTest(reach):
                self.assertEqual(self.run_script("{ Result(" + reach + ' + ""); }'), "undefined")

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

    def test_result_takes_one_text_and_the_last_one_counts(self):
        interpreter = js602.Interpreter()
        self.assertEqual(js602.evaluate(interpreter, '{ Result("prvni"); Result(1 + ""); }', lambda expression: "", ""), "1")
        self.assertIsNone(js602.evaluate(interpreter, '{ var a = 1; }', lambda expression: "", ""))

    def test_result_of_anything_else_stops_the_calculation(self):
        for call in ("Result()", "Result(1)", "Result('a', 'b')", "Result(true)", "Result(null)", "Result(undefined)", "Result(['a'])",
                     "Result('a'); Result(2)"):
            with self.subTest(call), self.assertRaises(js602.Thrown):
                self.run_script("{ " + call + "; }")
        self.assertEqual(self.run_script("{ try { Result(1); } catch (e) { Result(e.name); } }"), "TypeError")

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


@needs_node
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
    ({a: {b: 5}})?.a?.b; ({a: 1})?.x?.y.z; (null)?.a; (undefined)?.a.b.c; ({a: null}).a?.b; ({a: {b: 1}})?.["a"]?.b; (null)?.(); ({a: 1.5}).a?.toFixed(1); ({})?.a || "z"; true?.5:1; (function() { var n = 0; ({})?.a?.[n++]; return n; })();
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
        expected = json.loads(run_in_node(script, env={**os.environ, "TZ": "UTC"}))

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
