"""Code lists MPSV forms download when they open (codelists.py): taken only from MPSV's list service, kept for offline use."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import quoteattr

from lxml import etree
from support import page_data

import app
import certstore
import codelists
import public_services
from zfoengine import api as engine
from zfoengine import form_scripts

FORM = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:stat/><d:obec/><d:kod/><d:cizi/><d:Ciselnik_PrijemcePodani/><d:urad/><d:email/></d:root>
</fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="stat" nodeset="d:stat"/><fm:bind Id="obec" nodeset="d:obec"/><fm:bind Id="kod" nodeset="d:kod"/>
<fm:bind Id="cizi" nodeset="d:cizi"/><fm:bind Id="seznam" nodeset="d:Ciselnik_PrijemcePodani" depends="system" calculate="'PrijemcePodaniKraj'"/>
<fm:bind Id="urad" nodeset="d:urad"/>
<fm:bind Id="email" nodeset="d:email" depends="urad" calculate="GetListItemData('PrijemcePodani', '/d:root/d:urad', '@email')"/>
<fm:bind Id="pojistovna" nodeset="d:kod" calculate="GetListItemData('Pojistovny', ., 'itemtext')"/></fm:bind></fm:model>
<fm:enum name="Staty" updateurl='$(Eval("StrCat(GetCustomConfigValue(VratCiselnik),Stat.xml)"))'/>
<fm:enum name="PrijemcePodani" updatexsl="Rozdel"
  updateurl='$(Eval("StrCat(GetCustomConfigValue(url_base),fas-adapter-codelists/,DataXpath(//d:Ciselnik_PrijemcePodani),.xml)"))'/>
<fm:template name="Rozdel"><xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"><xsl:template match="/">
<fm:enum name="{fm:enum/@name}"><xsl:for-each select="//fm:list"><fm:list lid="{@lid}"><xsl:for-each select="fm:listitem">
<fm:listitem itemtext="{substring-after(substring-after(@itemtext, ';;'), ';;')}" value="{@value}" email="{substring-before(@itemtext, ';;')}"/>
</xsl:for-each></fm:list></xsl:for-each></fm:enum></xsl:template></xsl:stylesheet></fm:template>
<fm:enum name="Cizi" updateurl="https://attacker.example/seznam.xml"/>
<fm:enum name="Pojistovny" updateurl='$(Eval("StrCat(GetCustomConfigValue(VratCiselnik),ZdravotniPojistovna.xml)"))'/>
<fm:enum name="Rok" updateurl='$(Eval("StrCat(GetCustomConfigValue(VratCiselnik),Rok.xml)"))'/>
</fm:formparams>
<fo:layout-master-set><fo:simple-page-master master-name="A4"><fo:region-body/></fo:simple-page-master></fo:layout-master-set>
<fo:page-sequence master-reference="A4"><fo:flow flow-name="xsl-region-body"><fo:block>
<fo:inline form602ct="ilb" form602listname="Staty" form602bind="stat"/>
<fo:inline form602ct="ilb" form602listname="Cizi" form602bind="cizi"/>
<fo:inline form602ct="ilb" form602listname="PrijemcePodani" form602bind="urad"/>
<fo:inline form602ct="acm" form602bind="obec" updateurl="{ Naseptavac('ČÍSELNÍK - OBEC'); }"
  form602onchange=%s/>
</fo:block></fo:flow></fo:page-sequence>
</fo:root></dsig:Object></dsig:Signature>""" % quoteattr("{ GetDocument().GetElement().SetDataTo('kod', GetNamedParam('ACIItemId')); }")
STAT = ('<fm:enum xmlns:fm="http://software602.cz/forms" name="Stat" updateurl="" updatexsl=""><fm:list lid="1029">'
        '<fm:listitem itemtext="Česká republika" value="CZ"/><fm:listitem itemtext="Slovensko" value="SK"/></fm:list></fm:enum>')
KRAJ = ('<fm:enum xmlns:fm="http://software602.cz/forms" name="PrijemcePodaniKraj"><fm:list lid="1029">'
        '<fm:listitem itemtext="posta@kraj.example;;abc1234;;Krajský úřad" value="ku"/></fm:list></fm:enum>')
OBEC = ('<fm:enum xmlns:fm="http://software602.cz/forms" name="Obec"><fm:list lid="1029">'
        '<fm:listitem itemtext="Praha" value="554782"/><fm:listitem itemtext="Brno" value="582786"/></fm:list></fm:enum>')


class CodeLists(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.root = etree.fromstring(FORM.encode())
        self.data = {"//d:Ciselnik_PrijemcePodani": "PrijemcePodaniKraj"}

    def data_text(self, path):
        return self.data.get(path, "")

    def store(self, name, text):
        self.folder.mkdir(exist_ok=True)
        (self.folder / f"{name}.xml").write_text(text, encoding="utf-8")

    def test_only_lists_of_mpsvs_service_the_form_shows_or_reads_are_needed(self):
        self.assertEqual(codelists.needed(self.root, self.data_text), {"Stat", "Obec", "ZdravotniPojistovna", "PrijemcePodaniKraj"})

    def test_only_a_name_of_a_list_of_offices_goes_out_from_the_data(self):
        self.data["//d:Ciselnik_PrijemcePodani"] = "Jan Novák, Praha 4"
        self.assertNotIn("Jan Novák, Praha 4", codelists.needed(self.root, self.data_text))
        self.assertIn("PrijemcePodani", codelists.fill(self.root, {}, self.data_text))

    def test_stored_lists_fill_the_form_and_its_whisperer(self):
        self.store("Stat", STAT)
        self.store("Obec", OBEC)
        unfilled = codelists.fill(self.root, codelists.load(codelists.needed(self.root, self.data_text), self.folder), self.data_text)
        self.assertEqual(unfilled, {"ZdravotniPojistovna", "Cizi", "PrijemcePodaniKraj"})
        self.assertEqual(self.root.xpath("//fm:enum[@name='Staty']//fm:listitem/@value", namespaces={"fm": codelists.FM}), ["CZ", "SK"])
        whisperer = self.root.xpath("//*[@form602ct='acm']")[0]
        self.assertEqual(whisperer.get(form_scripts.WHISPERER_LIST_ATTRIBUTE), "blanketa-naseptavac-Obec")

    def test_without_the_internet_a_list_never_stored_is_missing(self):
        unfilled = codelists.fill(self.root, codelists.load(codelists.needed(self.root, self.data_text), self.folder), self.data_text)
        self.assertEqual(unfilled, {"Stat", "Obec", "ZdravotniPojistovna", "Cizi", "PrijemcePodaniKraj"})

    def test_a_list_the_form_cannot_reshape_is_offered_as_it_came(self):
        self.store("PrijemcePodaniKraj", KRAJ)
        self.root.find(".//{http://software602.cz/forms}template").getparent().remove(self.root.find(".//{http://software602.cz/forms}template"))
        codelists.fill(self.root, codelists.load({"PrijemcePodaniKraj"}, self.folder), self.data_text)
        self.assertEqual(self.root.xpath("//fm:enum[@name='PrijemcePodani']//fm:listitem/@itemtext", namespaces={"fm": codelists.FM}),
                         ["posta@kraj.example;;abc1234;;Krajský úřad"])

    def test_an_old_list_serves_and_is_renewed_for_next_time(self):
        self.store("Stat", STAT)
        old = time.time() - codelists.FRESH_SECONDS - 60
        os.utime(self.folder / "Stat.xml", (old, old))
        with mock.patch.object(codelists, "_refresh_later") as refresh:
            self.assertIn("Stat", codelists.load({"Stat"}, self.folder))
        refresh.assert_called_once_with(self.folder, "Stat")

    def test_opening_waits_for_missing_lists_only_so_long(self):
        def slow(folder, name):
            time.sleep(1)
            return codelists._items_of(STAT.encode(), "Stat")

        with mock.patch.object(codelists, "OPENING_WAIT_SECONDS", 0.1), mock.patch.object(codelists, "_fetch", side_effect=slow):
            started = time.monotonic()
            self.assertEqual(codelists.load({"Stat"}, self.folder), {})
            self.assertLess(time.monotonic() - started, 0.8)

    def test_a_background_download_reports_how_far_it_got(self):
        with mock.patch.object(codelists, "_fetch", return_value=None):
            token = codelists.start_download(["Stat", "Obec"], self.folder)
            for _ in range(100):
                if codelists.download_progress(token)["finished"]:
                    break
                time.sleep(0.01)
        self.assertEqual(codelists.download_progress(token), {"total": 2, "done": 2, "finished": True})
        self.assertIsNone(codelists.download_progress("neznamy"))

    def test_only_items_with_their_two_texts_are_taken_over(self):
        hostile = ('<fm:enum xmlns:fm="http://software602.cz/forms" name="Stat"><fm:list lid="1029" onload="x">'
                   '<fm:listitem itemtext="A" value="1" style="x"><script>alert(1)</script></fm:listitem></fm:list><cizi/></fm:enum>')
        items = codelists._items_of(hostile.encode(), "Stat")
        self.assertEqual(etree.tostring(items[0]).decode().count("script") + etree.tostring(items[0]).decode().count("style"), 0)
        self.assertEqual(items.xpath("//@*[name() != 'lid']", namespaces=None), ["Stat", "A", "1"])
        for other in ('<fm:enum xmlns:fm="http://software602.cz/forms" name="Jiny"><fm:list/></fm:enum>', "<enum name='Stat'/>",
                      '<fm:enum xmlns:fm="http://software602.cz/forms" name="Stat"/>',
                      '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><fm:enum xmlns:fm="http://software602.cz/forms" name="Stat"><fm:list>'
                      '<fm:listitem itemtext="&e;" value="1"/></fm:list></fm:enum>'):
            with self.subTest(other[:40]):
                try:
                    items = codelists._items_of(other.encode(), "Stat")
                except (ValueError, etree.LxmlError):
                    continue
                self.assertNotIn("root:", etree.tostring(items).decode())

    def test_download_takes_only_xml_from_the_known_address(self):
        class Answer:
            def __init__(self, content_type):
                self.headers = mock.Mock(get_content_type=mock.Mock(return_value=content_type))
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _size):
                return STAT.encode()

        for content_type, works in (("application/xml", True), ("text/html", False)):
            opener = mock.Mock(open=mock.Mock(return_value=Answer(content_type)))
            with self.subTest(content_type), mock.patch.dict(os.environ, {"ZFO_OFFLINE": ""}), \
                    mock.patch.object(public_services, "opener", return_value=opener):
                self.assertEqual(codelists._fetch(self.folder, "Stat") is not None, works)
                self.assertEqual(opener.open.call_args[0][0].full_url, "https://mpsv.gov.cz/fas-adapter-codelists/Stat.xml")
        self.assertIsNone(public_services._NoRedirects().redirect_request(None, None, 302, "Found", {}, "https://attacker.example/"))


class CodeListsOnThePage(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "formular.fo"
        self.path.write_text(FORM, encoding="utf-8")
        app.FORMS["codelists-test"] = self.path
        lists = Path(self.folder.name) / "codelists"
        lists.mkdir()
        (lists / "Stat.xml").write_text(STAT, encoding="utf-8")
        (lists / "Obec.xml").write_text(OBEC, encoding="utf-8")
        (lists / "PrijemcePodaniKraj.xml").write_text(KRAJ, encoding="utf-8")
        # signatures are not the point here, and a made-up form has no publisher's signature to describe
        for patcher in (mock.patch.object(certstore, "config_dir", return_value=Path(self.folder.name)),
                        mock.patch.object(app, "zfo_signatures", return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        del app.FORMS["codelists-test"]
        self.folder.cleanup()

    def open_form(self):
        return app.SESSIONS[page_data(app.render_zfo_page("codelists-test"))["session"]]["doc"]

    def test_list_becomes_a_choice_and_the_whisperer_offers_its_items(self):
        page = app.render_zfo_page("codelists-test")
        html = etree.HTML(page)
        self.assertEqual(html.xpath("//select[@name='stat']/option/@value")[-2:], ["CZ", "SK"])
        self.assertEqual(html.xpath("//input[@name='obec']/@list"), ["blanketa-naseptavac-Obec"])
        self.assertEqual(html.xpath("//datalist[@id='blanketa-naseptavac-Obec']/option/@value"), ["Praha", "Brno"])
        self.assertTrue(page_data(page)["info"]["missing_lists"])  # the list of attacker.example is never fetched

    def test_a_list_never_stored_shows_its_download_first(self):
        (Path(self.folder.name) / "codelists" / "Obec.xml").unlink()
        with mock.patch.dict(os.environ, {"ZFO_OFFLINE": ""}), mock.patch.object(codelists, "start_download", return_value="t1") as started:
            page = app.render_zfo_page("codelists-test")
            self.assertEqual(page_data(page), {"token": "t1"})
            self.assertEqual(started.call_args[0][0], ["Obec", "ZdravotniPojistovna"])
            self.assertIn("session", page_data(app.render_zfo_page("codelists-test", lists_waited_for=True)))

    def test_the_form_file_stays_as_the_office_published_it(self):
        app.render_zfo_page("codelists-test")
        self.assertEqual(self.path.read_text(encoding="utf-8"), FORM)

    def test_a_whispered_item_is_chosen_by_its_text(self):
        doc = self.open_form()
        for typed, code in (("Praha", "554782"), ("Prah", "")):
            with self.subTest(typed):
                engine.handle(doc, "store_val", {"field": "obec", "value": typed, "input_id": "obec", "repe": ""})
                self.assertEqual(doc.get_value("kod"), code)

    def test_the_office_is_chosen_by_its_name_and_the_form_learns_where_to_send(self):
        page = app.render_zfo_page("codelists-test")
        self.assertEqual(etree.HTML(page).xpath("//select[@name='urad']/option[@value='ku']/text()"), ["Krajský úřad"])
        doc = app.SESSIONS[page_data(page)["session"]]["doc"]
        engine.handle(doc, "store_val", {"field": "urad", "value": "ku", "input_id": "urad", "repe": ""})
        self.assertEqual(doc.get_value("email"), "posta@kraj.example")

    def test_without_the_list_the_typed_text_counts(self):
        (Path(self.folder.name) / "codelists" / "Obec.xml").unlink()
        doc = self.open_form()
        engine.handle(doc, "store_val", {"field": "obec", "value": "Prah", "input_id": "obec", "repe": ""})
        self.assertEqual(doc.get_value("kod"), "Prah")
