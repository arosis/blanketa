"""Files a user attaches to a ZFO form: the field keeps the file in the data and the form's script files it away."""
import base64
import unittest
from xml.sax.saxutils import quoteattr

import support  # noqa: F401  (puts the project on the import path)

from zfoengine import api as engine
from zfoengine import file_kinds, form_scripts

PDF = b"%PDF-1.3\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
SAVED_DATA = '<d:Data xmlns:d="http://software602.cz/sample"><d:poznamka>ze souboru</d:poznamka></d:Data>'.encode()

# What the Vložit soubor z počítače jako přílohu field of the older MPSV forms does with the file it was given.
FILE_AWAY = """
var ele = GetDocument().GetElement();
var field = "//*[@form602bind='Pom.Soubor.Data']";
var file = ele.GetBinData(field);
var megabytes = Math.round((ele.GetBinDataSize(field) / 1024) * 1000) / 1000000;
switch (ele.CheckBinData(".", MIMETYPE_CHECK | ANTIVIRUS_CHECK)) {
  case 0:
    ele.SetData("/d:root/d:Data/d:Priloha/d:Data", "Priloha.Data", "" + VariantToBase64(file));
    ele.SetData("/d:root/d:Data/d:Priloha/d:Nazev", "Priloha.Nazev", ele.DataXpath("/d:root/d:Pom/d:Soubor/d:Nazev"));
    ele.SetData("/d:root/d:Data/d:Priloha/d:Typ", "Priloha.Typ", ele.DataXpath("/d:root/d:Pom/d:Soubor/d:Typ"));
    ele.SetData("/d:root/d:Data/d:Priloha/@Velikost[1]", "Priloha.Velikost", megabytes.toString());
    break;
  case ERROR_BIN_DATA_UNKNOWN_FORMAT: MessageBox("Formát souboru nejde zjistit."); break;
  case ERROR_BIN_DATA_FORMAT_MISMATCH: MessageBox("Obsah souboru neodpovídá příponě."); break;
  case ERROR_BIN_DATA_FORMAT_NOT_ALLOWED: MessageBox("Tento typ souboru formulář nepřijímá."); break;
  case ERROR_BIN_DATA_INFECTED: MessageBox("Soubor je zavirovaný."); break;
  case ERROR_BIN_DATA_AVENGINE: MessageBox("Antivir neběží."); break;
}
ele.SetData("/d:root/d:Pom/d:Soubor/d:Data", "Pom.Soubor.Data", "");
ele.SetData("/d:root/d:Pom/d:Soubor/d:Nazev", "Pom.Soubor.Nazev", "");
ele.SetData("/d:root/d:Pom/d:Soubor/d:Typ", "Pom.Soubor.Typ", "");
"""
# The Načíst údaje field of the same forms reads the chosen file as text.
LOAD_SAVED_DATA = """
var doc = GetDocument(); var ele = doc.GetElement();
var file = ele.GetBinData("//*[@form602bind='Pom.Soubor.Data']");
if (ele.CheckBinData(".", MIMETYPE_CHECK) == 0 && file.data != "") doc.LoadFormDataPart(file, "/d:root/d:Data");
ele.SetData("/d:root/d:Pom/d:Soubor/d:Data", "Pom.Soubor.Data", "");
"""
FILTER = "PDF soubory (*.pdf)|*.pdf|PNG soubory (*.png)|*.png|XML soubory (*.xml)|*.xml"
FORM = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:Pom><d:Soubor><d:Data/><d:Nazev/><d:Typ/></d:Soubor></d:Pom>
<d:Data><d:poznamka/><d:Priloha Velikost=""><d:Data/><d:Nazev/><d:Typ/></d:Priloha></d:Data></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="Pom.Soubor.Data" nodeset="d:Pom/d:Soubor/d:Data"/>
<fm:bind Id="Pom.Soubor.Nazev" nodeset="d:Pom/d:Soubor/d:Nazev"/><fm:bind Id="Pom.Soubor.Typ" nodeset="d:Pom/d:Soubor/d:Typ"/>
<fm:bind Id="poznamka" nodeset="d:Data/d:poznamka"/>
<fm:bind Id="Priloha.Data" nodeset="d:Data/d:Priloha/d:Data"/><fm:bind Id="Priloha.Nazev" nodeset="d:Data/d:Priloha/d:Nazev"/>
<fm:bind Id="Priloha.Typ" nodeset="d:Data/d:Priloha/d:Typ"/><fm:bind Id="Priloha.Velikost" nodeset="d:Data/d:Priloha/@Velikost"/>
</fm:bind></fm:model></fm:formparams>
<fo:inline form602ct="txt" form602bind="poznamka"/>
<fo:block form602ct="bin" form602bind="Pom.Soubor.Data" filenamebind="Pom.Soubor.Nazev" mediatypebind="Pom.Soubor.Typ"
 form602menu="add" form602ffilter="%s" %s form602onchange=%s>Vložit soubor</fo:block>
<fo:block form602ct="bin" form602bind="Priloha.Data" filenamebind="Priloha.Nazev" mediatypebind="Priloha.Typ" form602menu="save">Stáhnout</fo:block>
</fo:root></dsig:Object></dsig:Signature>"""


def form(script=FILE_AWAY, file_filter=FILTER, strict=True):
    return engine.open_document((FORM % (file_filter, 'form602ffiltertype="strict"' if strict else "", quoteattr("{" + script + "}"))).encode())


def value(doc, bind_id):
    return doc.get_value(bind_id) or ""


def chosen(name, content):
    return {"name": name, "content": base64.b64encode(content).decode()}


FIELD_THAT_TAKES_A_FILE, FIELD_THAT_GIVES_ITS_FILE = 0, 1


def attach(doc, name, content, answers=()):
    return form_scripts.attach_file(doc, FIELD_THAT_TAKES_A_FILE, "", list(answers), None, chosen(name, content))


class FilesAttachedToAForm(unittest.TestCase):
    def test_the_form_files_the_attachment_away_as_form_filler_stores_it(self):
        doc = form()
        result = attach(doc, "Plná moc.pdf", PDF)
        self.assertEqual(result["messages"], [])
        self.assertTrue(result["changed"])
        self.assertEqual(value(doc, "Priloha.Data"), base64.b64encode(PDF).decode())
        self.assertEqual((value(doc, "Priloha.Nazev"), value(doc, "Priloha.Typ")), ("Plná moc.pdf", "pdf"))
        self.assertEqual(value(doc, "Priloha.Velikost"), "0.000034")
        self.assertEqual(value(doc, "Pom.Soubor.Data"), "")

    def test_without_a_script_the_file_stays_in_the_field(self):
        doc = engine.open_document((FORM % (FILTER, "", '""')).encode())
        self.assertTrue(attach(doc, "Sken.PNG", PNG)["changed"])
        self.assertEqual(base64.b64decode(value(doc, "Pom.Soubor.Data")), PNG)
        self.assertEqual((value(doc, "Pom.Soubor.Nazev"), value(doc, "Pom.Soubor.Typ")), ("Sken.PNG", "png"))

    def test_a_file_whose_content_is_not_what_its_extension_says_does_not_get_in(self):
        doc = form()
        result = attach(doc, "obrazek.png", PDF)
        self.assertEqual(result["messages"], [{"text": "Obsah souboru neodpovídá příponě.", "title": ""}])
        self.assertEqual((value(doc, "Priloha.Data"), value(doc, "Pom.Soubor.Data")), ("", ""))

    def test_the_form_hears_about_a_type_it_did_not_list_and_about_a_file_with_no_type(self):
        for name, content, message in (("tabulka.xlsx", b"PK\x03\x04", "Tento typ souboru formulář nepřijímá."),
                                       ("bez pripony", PDF, "Formát souboru nejde zjistit."),
                                       ("prazdny.pdf", b"", "Formát souboru nejde zjistit.")):
            with self.subTest(name):
                self.assertEqual(attach(form(strict=False), name, content)["messages"], [{"text": message, "title": ""}])

    def test_a_field_with_a_strict_filter_takes_only_the_listed_types(self):
        doc = form()
        self.assertEqual(attach(doc, "tabulka.xlsx", b"PK\x03\x04"), {"stopped": "Formulář tu přijímá jen soubory typu PDF, PNG, XML."})
        self.assertEqual(value(doc, "Pom.Soubor.Data"), "")

    def test_the_user_learns_that_no_antivirus_looked_at_the_file(self):
        self.assertEqual(attach(form(), "a.pdf", PDF)["notices"], [form_scripts.NO_ANTIVIRUS_NOTICE])
        self.assertEqual(attach(form(LOAD_SAVED_DATA), "data.xml", SAVED_DATA)["notices"], [])

    def test_a_file_too_large_for_the_office_is_refused_before_it_reaches_the_data(self):
        doc = form()
        result = attach(doc, "velky.pdf", PDF + bytes(form_scripts.MAX_ATTACHED_BYTES))
        self.assertEqual(result, {"stopped": "Soubor je větší než 17 MB, formulář ho nepřijme."})
        self.assertEqual(value(doc, "Pom.Soubor.Data"), "")

    def test_a_file_of_several_megabytes_goes_through_the_script(self):
        doc = form()
        large = PDF + bytes(5_000_000)
        self.assertEqual(attach(doc, "velky.pdf", large)["messages"], [])
        self.assertEqual(base64.b64decode(value(doc, "Priloha.Data")), large)

    def test_a_script_that_stops_leaves_no_file_behind(self):
        doc = form("GetDocument().GetElement().GetBinData('.'); GetDocument().NeznamaFunkce();")
        self.assertIn("refused", attach(doc, "a.pdf", PDF))
        self.assertEqual((value(doc, "Pom.Soubor.Data"), value(doc, "Pom.Soubor.Nazev")), ("", ""))

    def test_a_question_of_the_script_is_asked_and_the_file_comes_again_with_the_answer(self):
        doc = form("if (MessageBox('Opravdu vložit?', '', MB_YESNO) == IDYES) {" + FILE_AWAY + "}")
        question = attach(doc, "a.pdf", PDF)["question"]
        self.assertEqual(question["retry"]["attach"], chosen("a.pdf", PDF))
        self.assertEqual(value(doc, "Pom.Soubor.Data"), "")
        answered = engine.handle(doc, "run_form_script", {**question["retry"], "answers": [True]})
        self.assertIn("form_script_callback", str(answered))
        self.assertEqual(value(doc, "Priloha.Nazev"), "a.pdf")

    def test_saved_data_chosen_as_a_file_fill_the_form(self):
        doc = form(LOAD_SAVED_DATA)
        result = attach(doc, "ulozeno.xml", b"\xef\xbb\xbf" + SAVED_DATA)
        self.assertEqual(result["values"]["poznamka"], "ze souboru")
        self.assertEqual(value(doc, "Pom.Soubor.Data"), "")

    def test_the_form_learns_that_the_saved_data_were_loaded(self):
        script = LOAD_SAVED_DATA.replace('doc.LoadFormDataPart(file, "/d:root/d:Data");',
                                         'if (doc.LoadFormDataPart(file, "/d:root/d:Data")) MessageBox("Načteno."); else MessageBox("Nenačteno.");')
        self.assertEqual(attach(form(script), "ulozeno.xml", SAVED_DATA)["messages"], [{"text": "Načteno.", "title": ""}])

    def test_only_a_field_that_takes_files_takes_one_and_only_a_plainly_named_file(self):
        doc = form()
        for position in (FIELD_THAT_GIVES_ITS_FILE, 2, -1, "0", None):
            with self.subTest(position):
                self.assertIsNone(form_scripts.attach_file(doc, position, "", [], None, chosen("a.pdf", PDF)))
        for file in (chosen("../a.pdf", PDF), chosen("a\\b.pdf", PDF), {"name": "a.pdf", "content": "není base64"}, {"name": "a.pdf"}, None):
            with self.subTest(file):
                self.assertIsNone(form_scripts.attach_file(doc, FIELD_THAT_TAKES_A_FILE, "", [], None, file))
        self.assertEqual(value(doc, "Pom.Soubor.Data"), "")

    def test_only_what_came_from_an_attached_file_turns_into_base64(self):
        doc = form("var v = new Variant(); v.data = 'text'; GetDocument().GetElement().SetDataTo('poznamka', VariantToBase64(v));")
        self.assertEqual(attach(doc, "a.pdf", PDF)["refused"], "VariantToBase64")

    def test_a_script_reads_a_file_only_from_a_field_for_files(self):
        doc = form("GetDocument().GetElement().GetBinData(\"//*[@form602bind='poznamka']\");")
        self.assertEqual(attach(doc, "a.pdf", PDF)["refused"], "element.GetBinData")


class KindsOfAttachedFiles(unittest.TestCase):
    def test_the_filter_of_a_field_names_the_extensions_it_takes(self):
        self.assertEqual(file_kinds.extensions_in_filter("PDF soubory (*.pdf)|*.pdf|Obrázky|*.JPG;*.jpeg"), {"pdf", "jpg", "jpeg"})
        self.assertEqual(file_kinds.extensions_in_filter(None), set())

    def test_known_kinds_are_told_by_how_their_content_starts(self):
        for name, content in (("a.pdf", PDF), ("a.PNG", PNG), ("a.jpg", b"\xff\xd8\xff\xe0"), ("a.gif", b"GIF89a"), ("a.docx", b"PK\x03\x04"),
                              ("a.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"), ("a.rtf", b"{\\rtf1"), ("a.xml", b"\xff\xfe<\x00a\x00/\x00>\x00"),
                              ("a.xml", b"  <a/>"), ("a.txt", b"cokoli")):
            with self.subTest(name):
                self.assertIsNone(file_kinds.fault_of_kind(name, content, set()))
        for name, content in (("a.pdf", PNG), ("a.xml", b"text"), ("a.jpeg", PDF)):
            with self.subTest(name):
                self.assertEqual(file_kinds.fault_of_kind(name, content, set()), file_kinds.FORMAT_MISMATCH)
