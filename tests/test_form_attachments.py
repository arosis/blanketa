"""Files a user attaches to a ZFO form: the field keeps the file in the data and the form's script files it away."""
import base64
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from xml.sax.saxutils import quoteattr

import support  # noqa: F401  (puts the project on the import path)
from lxml import etree
from lxml import html as lxml_html

import app
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
<fo:page-sequence master-reference="p"><fo:flow flow-name="xsl-region-body">
<fo:block><fo:inline form602ct="txt" form602bind="poznamka"/></fo:block>
<fo:block form602ct="bin" form602bind="Pom.Soubor.Data" filenamebind="Pom.Soubor.Nazev" mediatypebind="Pom.Soubor.Typ"
 form602menu="add" form602ffilter="%s" %s form602onchange=%s>Vložit soubor</fo:block>
<fo:block form602ct="bin" form602bind="Priloha.Data" filenamebind="Priloha.Nazev" mediatypebind="Priloha.Typ" form602menu="save">Stáhnout</fo:block>
</fo:flow></fo:page-sequence></fo:root></dsig:Object></dsig:Signature>"""


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
        self.assertEqual((value(doc, "Pom.Soubor.Nazev"), value(doc, "Pom.Soubor.Typ")), ("Sken.PNG", "PNG"))

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

    def test_a_file_far_beyond_what_a_form_takes_is_not_even_read(self):
        doc = form()
        beyond_reading = {"name": "a.pdf", "content": "A" * (form_scripts.MAX_ATTACHED_CHARACTERS + 4)}
        self.assertIsNone(form_scripts.attach_file(doc, FIELD_THAT_TAKES_A_FILE, "", [], None, beyond_reading))

    def test_the_form_gets_the_type_of_the_file_as_its_name_has_it(self):
        # Form Filler 4.81 stores <Typ>PDF</Typ> for "Sken Velky.PDF".
        doc = form()
        self.assertEqual(attach(doc, "Sken Velky.PDF", PDF)["messages"], [])
        self.assertEqual((value(doc, "Priloha.Nazev"), value(doc, "Priloha.Typ")), ("Sken Velky.PDF", "PDF"))

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
        self.assertEqual(file_kinds.extensions_in_filter("Cokoli|*.*|Dlouhé|*.priponadelsineznormalni|Znaky|*.<b>;*.docx"), {"docx"})

    def test_known_kinds_are_told_by_how_their_content_starts(self):
        for name, content in (("a.pdf", PDF), ("a.PNG", PNG), ("a.jpg", b"\xff\xd8\xff\xe0"), ("a.gif", b"GIF89a"), ("a.docx", b"PK\x03\x04"),
                              ("a.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"), ("a.rtf", b"{\\rtf1"), ("a.xml", b"\xff\xfe<\x00a\x00/\x00>\x00"),
                              ("a.xml", b"  <a/>"), ("a.txt", b"cokoli")):
            with self.subTest(name):
                self.assertIsNone(file_kinds.fault_of_kind(name, content, set()))
        for name, content in (("a.pdf", PNG), ("a.xml", b"text"), ("a.jpeg", PDF)):
            with self.subTest(name):
                self.assertEqual(file_kinds.fault_of_kind(name, content, set()), file_kinds.FORMAT_MISMATCH)


class FilesAFieldHolds(unittest.TestCase):
    def test_a_field_gives_back_the_file_and_the_name_it_was_given(self):
        doc = engine.open_document((FORM % (FILTER, "", '""')).encode())
        attach(doc, "Sken.PNG", PNG)
        self.assertEqual(form_scripts.file_of_field(doc, FIELD_THAT_TAKES_A_FILE, ""), ("Sken.PNG", PNG))

    def test_an_empty_field_and_a_field_that_does_not_exist_give_nothing(self):
        doc = form()
        for position in (FIELD_THAT_TAKES_A_FILE, FIELD_THAT_GIVES_ITS_FILE, 2, -1, "0", None):
            with self.subTest(position):
                self.assertIsNone(form_scripts.file_of_field(doc, position, ""))

    def test_a_name_from_the_data_that_is_no_plain_file_name_is_not_passed_on(self):
        doc = form()
        attach(doc, "a.pdf", PDF)
        engine.handle(doc, "store_val", {"field": "Priloha.Nazev", "value": "../../.ssh/config", "repe": "", "input_id": "x"})
        self.assertEqual(form_scripts.file_of_field(doc, FIELD_THAT_GIVES_ITS_FILE, ""), (form_scripts.NAME_OF_UNNAMED_ATTACHMENT, PDF))


class FileFieldsOnThePage(unittest.TestCase):
    def test_a_field_for_a_file_is_a_button_that_takes_a_file_or_gives_its_file_back(self):
        root = etree.fromstring((FORM % (FILTER, "", '""')).encode())
        form_scripts.name_file_fields_for_page(root)
        page = lxml_html.fromstring(etree.tostring(app.XSLT(etree.ElementTree(root), PATH_TO_JS="'/s/'", PATH_TO_CSS="'/s/'"),
                                                   method="html", encoding="unicode"))
        buttons = [(button.text_content().strip(), button.get("data-file-field"), button.get("onclick"), button.get("data-file-filter"))
                   for button in page.xpath("//button[@class='zfo-file-field']")]
        self.assertEqual(buttons, [("Vložit soubor", "0", "attach_form_file(this)", FILTER), ("Stáhnout", "1", "save_form_file(this)", None)])
        self.assertEqual(page.xpath("//input[@class='fileInput'] | //button[@name='add_attachment']"), [])


class SavingTheFileOfAField(unittest.TestCase):
    """The hosts save what the server serves under /attachment with a number; the page asks for the file of a field first."""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request(method, path, body=body, headers={"Host": f"127.0.0.1:{self.port}"})
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Disposition")

    def test_the_file_the_user_attached_comes_back_under_the_number_the_server_gave_it(self):
        session = app.new_session({"name": "s-prilohou", "doc": engine.open_document((FORM % (FILTER, "", '""')).encode())})
        ask = json.dumps({"file_field": FIELD_THAT_TAKES_A_FILE, "repe": ""}).encode()
        self.assertEqual(self.request("POST", f"/file-of-field?s={session}", ask)[0], 404)
        attached = json.dumps({"type": "run_form_script", "data": {"file_field": FIELD_THAT_TAKES_A_FILE, "repe": "", "attach": chosen("Plná moc.pdf", PDF)}})
        self.assertEqual(self.request("POST", f"/ipc?s={session}", attached.encode())[0], 200)
        status, answer, _ = self.request("POST", f"/file-of-field?s={session}", ask)
        self.assertEqual((status, json.loads(answer)), (200, {"index": 1, "name": "Plná moc.pdf"}))
        self.assertEqual(self.request("GET", f"/attachment?s={session}&i=1"), (200, PDF, "attachment; filename*=UTF-8''Pln%C3%A1%20moc.pdf"))
        for path in (f"/attachment?s={session}&i=0", f"/attachment?s={session}&i=2", "/attachment?s=jina&i=1"):
            with self.subTest(path):
                self.assertEqual(self.request("GET", path)[0], 404)
        for body in (b"", b"{", b"[]", b"3", b'"x"', json.dumps({"file_field": FIELD_THAT_TAKES_A_FILE, "repe": 5}).encode(),
                     json.dumps({"file_field": FIELD_THAT_GIVES_ITS_FILE}).encode(), json.dumps({"file_field": "0"}).encode()):
            with self.subTest(body):
                self.assertEqual(self.request("POST", f"/file-of-field?s={session}", body)[0], 404)

    def test_files_asked_for_one_after_another_stay_ready_until_the_oldest_gives_way(self):
        session = app.new_session({"name": "s-prilohou", "doc": engine.open_document((FORM % (FILTER, "", '""')).encode())})
        attached = json.dumps({"type": "run_form_script", "data": {"file_field": FIELD_THAT_TAKES_A_FILE, "repe": "", "attach": chosen("a.pdf", PDF)}})
        self.request("POST", f"/ipc?s={session}", attached.encode())
        ask = json.dumps({"file_field": FIELD_THAT_TAKES_A_FILE}).encode()
        numbers = [json.loads(self.request("POST", f"/file-of-field?s={session}", ask)[1])["index"] for _ in range(app.MAX_FILES_PREPARED_TO_SAVE + 1)]
        self.assertEqual(self.request("GET", f"/attachment?s={session}&i={numbers[0]}")[0], 404)
        for number in numbers[1:]:
            with self.subTest(number):
                self.assertEqual(self.request("GET", f"/attachment?s={session}&i={number}")[:2], (200, PDF))


# The newer MPSV forms, written for FormApps Server: a list of attachments and fields for one attachment each.
JUDGE_LISTED_FILE = """
var ele = GetDocument().GetElement();
if (ele.CheckBinData(".", MIMETYPE_CHECK | ANTIVIRUS_CHECK) != 0) {
  MessageBox("Soubor nebyl vložen.");
  if (!ele.RemoveSection(".")) {
    ele.SetData("../d:filename", "att.filename", ""); ele.SetBinData(".", new Variant()); ele.SetData("../../d:empty", "empty", "1");
  }
}
"""
REMOVE_LISTED_FILE = """
var ele = GetDocument().GetElement();
if (!ele.RemoveSection(".")) {
  ele.SetData("../d:filename", "att.filename", ""); ele.SetBinData("ancestor::*[@form602ct='rep'][1]//fo:basic-link", new Variant());
  ele.SetData("../d:size", "att.size", ""); ele.SetData("../../d:empty", "empty", "1");
}
"""
UPLOAD_ONE_FILE = """
var ele = GetDocument().GetElement();
var link = "ancestor::*[@form602id='state1']//*[@form602id='data2']";
var data = ele.GetUploadedFileData();
if (data.data) {
  ele.SetData("../d:error", "s1.error", "");
  ele.SetBinData(link, data);
  if (ele.CheckBinData(link, MIMETYPE_CHECK | ANTIVIRUS_CHECK) != 0) { ele.SetBinData(link, new Variant()); ele.SetData("../d:error", "s1.error", "Soubor nebyl vložen."); }
} else ele.SetData("../d:error", "s1.error", "Chyba při nahrávání.");
"""
NEWER_FORM = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:attachments>
<d:attachment><d:data/><d:filename/><d:size/><d:extension/><d:timestamp>0</d:timestamp></d:attachment><d:empty>1</d:empty></d:attachments>
<d:attachment1><d:data/><d:filename/><d:size/><d:extension/><d:error/></d:attachment1></d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="att" nodeset="d:attachments/d:attachment"><fm:bind Id="att.data" nodeset="d:data"/>
<fm:bind Id="att.filename" nodeset="d:filename"/><fm:bind Id="att.size" nodeset="d:size"/><fm:bind Id="att.extension" nodeset="d:extension"/>
<fm:bind Id="att.timestamp" nodeset="d:timestamp"/></fm:bind><fm:bind Id="empty" nodeset="d:attachments/d:empty"/>
<fm:bind Id="s1.data" nodeset="d:attachment1/d:data"/><fm:bind Id="s1.filename" nodeset="d:attachment1/d:filename"/>
<fm:bind Id="s1.size" nodeset="d:attachment1/d:size"/><fm:bind Id="s1.extension" nodeset="d:attachment1/d:extension"/>
<fm:bind Id="s1.error" nodeset="d:attachment1/d:error"/></fm:bind></fm:model></fm:formparams>
<fo:page-sequence master-reference="p"><fo:flow flow-name="xsl-region-body">
<fo:block fa-data-id="attachments-section">
<fo:inline form602ct="btm" form602bindref="att.data" form602attchsec="ancestor::*[@fa-data-id='attachments-section']//*[@form602ct='rep']"
 form602maxallfilessize="700" form602maxonefilesize="400" form602maxoccurs="3" form602webaccept=".pdf,.png" form602webaccepttype="strict"
 form602attchuploadstartaction="{ GetDocument().GetElement().SetDataTo('empty', '0'); }">Nahrajte ze zařízení</fo:inline>
<fo:block form602ct="rep" form602bind="att">
<fo:basic-link form602ct="bil" form602bindref="att.data" filenamebind="att.filename" filesizebind="att.size" mediatypebind="att.extension"
 form602onchange=%s/>
<fo:inline form602ct="out" form602bindref="att.data" form602format="attch-size-kB"/>
<fo:inline form602ct="btn" form602bindref="att.data" form602btnclick=%s>Odebrat</fo:inline>
</fo:block></fo:block>
<fo:block form602id="state1">
<fo:inline form602ct="bib" form602bindref="s1.data" form602maxsize="400" form602webaccept=".pdf" form602btnclick=%s>Nahrát soubor</fo:inline>
<fo:basic-link form602ct="bil" form602id="data2" form602bindref="s1.data" filenamebind="s1.filename" filesizebind="s1.size" mediatypebind="s1.extension"/>
</fo:block>
</fo:flow></fo:page-sequence></fo:root></dsig:Object></dsig:Signature>"""
REMOVE_BUTTON, UPLOAD_BUTTON = 0, 1


def newer_form(upload=UPLOAD_ONE_FILE):
    return engine.open_document((NEWER_FORM % tuple(quoteattr("{" + script + "}") for script in (JUDGE_LISTED_FILE, REMOVE_LISTED_FILE, upload))).encode())


def listed(doc, part):
    return [node.text or "" for node in doc._get_data_root().xpath(f"//d:attachment/d:{part}", namespaces={"d": "http://software602.cz/sample"})]


def add_to_list(doc, name, content):
    return form_scripts.add_file_to_list(doc, 0, "", [], None, chosen(name, content))


def upload(doc, name, content):
    return form_scripts.run_button_script(doc, UPLOAD_BUTTON, "", [], None, chosen(name, content))


class ListsOfAttachments(unittest.TestCase):
    def test_each_file_gets_an_item_written_as_the_web_application_of_mpsv_writes_it(self):
        # Seen in the data its form no. 362 saved in November 2026: the size in bytes, the extension as the name has it, timestamp 0.
        doc = newer_form()
        self.assertEqual(add_to_list(doc, "Plna moc.pdf", PDF)["messages"], [])
        self.assertIn("sections", add_to_list(doc, "Sken Velky.PNG", PNG))
        self.assertEqual(listed(doc, "filename"), ["Plna moc.pdf", "Sken Velky.PNG"])
        self.assertEqual(listed(doc, "size"), [str(len(PDF)), str(len(PNG))])
        self.assertEqual(listed(doc, "extension"), ["pdf", "PNG"])
        self.assertEqual(listed(doc, "timestamp"), ["0", "0"])
        self.assertEqual([base64.b64decode(data) for data in listed(doc, "data")], [PDF, PNG])
        self.assertEqual(value(doc, "empty"), "0")

    def test_the_form_judges_each_file_and_takes_a_bad_one_out_again(self):
        doc = newer_form()
        add_to_list(doc, "Plna moc.pdf", PDF)
        self.assertEqual(add_to_list(doc, "podvrh.pdf", PNG)["messages"], [{"text": "Soubor nebyl vložen.", "title": ""}])
        self.assertEqual(listed(doc, "filename"), ["Plna moc.pdf"])
        lone = newer_form()
        add_to_list(lone, "podvrh.pdf", PNG)
        self.assertEqual((listed(lone, "filename"), listed(lone, "data"), value(lone, "empty")), ([""], [""], "1"))

    def test_the_list_takes_only_what_its_button_allows(self):
        doc = newer_form()
        for name, content, refusal in (("tabulka.xlsx", b"PK\x03\x04", "Formulář tu přijímá jen soubory typu PDF, PNG."),
                                       ("velky.pdf", PDF + bytes(400), "Soubor je větší než 400\u00a0B, formulář ho nepřijme.")):
            with self.subTest(name):
                self.assertEqual(add_to_list(doc, name, content), {"stopped": refusal})
        for name in ("a.pdf", "b.pdf"):
            add_to_list(doc, name, PDF + bytes(200))
        self.assertEqual(add_to_list(doc, "c.pdf", PDF + bytes(300)), {"stopped": "Přílohy by dohromady měly víc než 700\u00a0B, formulář soubor nepřijme."})
        add_to_list(doc, "c.pdf", PDF)
        self.assertEqual(add_to_list(doc, "d.pdf", PDF), {"stopped": "Formulář přijme nejvýš 3 příloh."})
        self.assertEqual(listed(doc, "filename"), ["a.pdf", "b.pdf", "c.pdf"])

    def test_the_form_takes_files_out_of_the_list_down_to_an_empty_one(self):
        doc = newer_form()
        add_to_list(doc, "a.pdf", PDF)
        add_to_list(doc, "b.pdf", PDF)
        form_scripts.run_button_script(doc, REMOVE_BUTTON, "block---att-rep-0", [])
        self.assertEqual(listed(doc, "filename"), ["b.pdf"])
        form_scripts.run_button_script(doc, REMOVE_BUTTON, "block---att-rep-0", [])
        self.assertEqual((listed(doc, "filename"), listed(doc, "data"), listed(doc, "size"), value(doc, "empty")), ([""], [""], [""], "1"))
        add_to_list(doc, "c.pdf", PDF)
        self.assertEqual(listed(doc, "filename"), ["c.pdf"])

    def test_an_item_that_holds_a_file_is_never_taken_for_the_empty_one(self):
        doc = newer_form()
        for bind_id, text in (("att.filename", "puvodni.pdf"), ("att.data", base64.b64encode(PDF).decode())):
            engine.handle(doc, "store_val", {"field": bind_id, "value": text, "repe": "block---att-rep-0", "input_id": bind_id})
        add_to_list(doc, "novy.pdf", PDF)
        self.assertEqual(listed(doc, "filename"), ["puvodni.pdf", "novy.pdf"])

    def test_a_question_of_the_form_is_asked_and_the_file_comes_again_with_the_answer(self):
        asking = NEWER_FORM.replace('form602attchuploadstartaction="{', 'form602attchuploadstartaction="{ if (MessageBox(\'Vložit?\', \'\', MB_YESNO) != IDYES) NeznamaFunkce();')
        doc = engine.open_document((asking % tuple(quoteattr("{" + script + "}") for script in (JUDGE_LISTED_FILE, REMOVE_LISTED_FILE, UPLOAD_ONE_FILE))).encode())
        question = add_to_list(doc, "a.pdf", PDF)["question"]
        self.assertEqual((question["retry"]["file_list"], listed(doc, "filename")), (0, [""]))
        engine.handle(doc, "run_form_script", {**question["retry"], "answers": [True]})
        self.assertEqual(listed(doc, "filename"), ["a.pdf"])

    def test_only_a_list_button_takes_files_for_a_list(self):
        doc = newer_form()
        for position in (1, -1, "0", None):
            with self.subTest(position):
                self.assertIsNone(form_scripts.add_file_to_list(doc, position, "", [], None, chosen("a.pdf", PDF)))
        self.assertIsNone(form_scripts.add_file_to_list(doc, 0, "", [], None, {"name": "../a.pdf", "content": ""}))


class FieldsForOneAttachment(unittest.TestCase):
    def test_the_script_of_the_upload_button_puts_the_file_into_its_link(self):
        doc = newer_form()
        result = upload(doc, "Potvrzeni.PDF", PDF)
        self.assertEqual((result["messages"], result["notices"]), ([], [form_scripts.NO_ANTIVIRUS_NOTICE]))
        self.assertEqual((value(doc, "s1.filename"), value(doc, "s1.size"), value(doc, "s1.extension")), ("Potvrzeni.PDF", str(len(PDF)), "PDF"))
        self.assertEqual(base64.b64decode(value(doc, "s1.data")), PDF)

    def test_a_file_the_form_judges_bad_leaves_the_field_empty_and_its_error_written(self):
        doc = newer_form()
        upload(doc, "Potvrzeni.pdf", PDF)
        upload(doc, "podvrh.pdf", PNG)
        self.assertEqual((value(doc, "s1.filename"), value(doc, "s1.data"), value(doc, "s1.size")), ("", "", ""))
        self.assertEqual(value(doc, "s1.error"), "Soubor nebyl vložen.")

    def test_the_button_takes_only_the_types_and_the_size_it_names(self):
        doc = newer_form()
        self.assertEqual(upload(doc, "sken.png", PNG), {"stopped": "Formulář tu přijímá jen soubory typu PDF."})
        self.assertEqual(upload(doc, "velky.pdf", PDF + bytes(400)), {"stopped": "Soubor je větší než 400\u00a0B, formulář ho nepřijme."})
        self.assertEqual(value(doc, "s1.data"), "")

    def test_only_an_upload_button_gets_a_file_and_only_a_file_goes_into_a_link(self):
        doc = newer_form()
        self.assertIsNone(form_scripts.run_button_script(doc, REMOVE_BUTTON, "block---att-rep-0", [], None, chosen("a.pdf", PDF)))
        made_up = newer_form("var v = new Variant(); v.data = 'text'; GetDocument().GetElement().SetBinData(\"//*[@form602id='data2']\", v);")
        self.assertEqual(upload(made_up, "a.pdf", PDF)["refused"], "element.SetBinData")
        no_link = newer_form("var e = GetDocument().GetElement(); e.SetBinData(\"//*[@form602ct='bib']\", e.GetUploadedFileData());")
        self.assertEqual(upload(no_link, "a.pdf", PDF)["refused"], "element.SetBinData")


class NewerAttachmentsOnThePage(unittest.TestCase):
    def test_the_buttons_take_files_and_the_link_of_an_attachment_gives_its_file_back(self):
        root = etree.fromstring((NEWER_FORM % tuple(quoteattr("{" + script + "}") for script in (JUDGE_LISTED_FILE, REMOVE_LISTED_FILE, UPLOAD_ONE_FILE))).encode())
        form_scripts.name_buttons_for_page(root)
        form_scripts.name_file_fields_for_page(root)
        form_scripts.name_what_attachments_show(root)
        page = lxml_html.fromstring(etree.tostring(app.XSLT(etree.ElementTree(root), PATH_TO_JS="'/s/'", PATH_TO_CSS="'/s/'"),
                                                   method="html", encoding="unicode"))
        self.assertEqual([(button.text_content().strip(), button.get("data-file-list"), button.get("data-form-upload"))
                          for button in page.xpath("//button[@onclick='attach_files_to_list(this)']")], [("Nahrajte ze zařízení", "0", ".pdf,.png")])
        self.assertEqual([(button.text_content().strip(), button.get("data-form-button"), button.get("data-form-upload"))
                          for button in page.xpath("//button[@data-form-upload][@onclick='run_form_button(this)']")], [("Nahrát soubor", "1", ".pdf")])
        self.assertEqual([(link.get("name"), link.get("data-file-field"), link.get("class")) for link in page.xpath("//button[@onclick='save_form_file(this)']")],
                         [("att.filename", "0", "zfo-file-field outputelement"), ("s1.filename", "1", "zfo-file-field outputelement")])

    def test_the_link_of_an_attachment_holds_the_file_of_its_item(self):
        doc = newer_form()
        add_to_list(doc, "a.pdf", PDF)
        add_to_list(doc, "b.png", PNG)
        upload(doc, "c.pdf", PDF)
        self.assertEqual(form_scripts.file_of_field(doc, 0, "block---att-rep-1"), ("b.png", PNG))
        self.assertEqual(form_scripts.file_of_field(doc, 1, ""), ("c.pdf", PDF))

    def test_each_row_of_the_list_shows_the_name_and_the_size_of_its_own_file_and_never_the_file_itself(self):
        doc = newer_form()
        add_to_list(doc, "a.pdf", PDF)
        add_to_list(doc, "b.png", PNG)
        self.assertEqual(doc.get_repeatable_sections()["att"], [{"att.filename": "a.pdf", "att.size": str(len(PDF))},
                                                                 {"att.filename": "b.png", "att.size": str(len(PNG))}])
        self.assertNotIn("att.data", form_scripts._page_values(doc))
        root = etree.fromstring((NEWER_FORM % ('""', '""', '""')).encode())
        form_scripts.name_what_attachments_show(root)
        page = lxml_html.fromstring(etree.tostring(app.XSLT(etree.ElementTree(root), PATH_TO_JS="'/s/'", PATH_TO_CSS="'/s/'"),
                                                   method="html", encoding="unicode"))
        self.assertEqual([(size.get("name"), size.get("class")) for size in page.xpath("//*[@data-format='attch-size-kB']")], [("att.size", "outputelement")])

    def test_a_link_shows_a_file_and_takes_none(self):
        doc = newer_form()
        self.assertIsNone(form_scripts.attach_file(doc, 0, "", [], None, chosen("a.pdf", PDF)))
        self.assertEqual(listed(doc, "filename"), [""])
