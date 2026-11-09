"""Addresses of MPSV forms checked in RÚIAN (ruian.py): collected from the form's data, sent to ČÚZK only as an address."""
import io
import os
import unittest
import urllib.parse
from unittest import mock
from xml.sax.saxutils import quoteattr

import support  # noqa: F401 - puts the program on the path

import public_services
import ruian
from zfoengine import api as engine
from zfoengine import form_scripts

EMPTY = dict.fromkeys(form_scripts.ADDRESS_PARTS, "")
PRAGUE = {**EMPTY, "Ulice": "Na Poříčním právu", "CisloPopisne": "376", "CisloOrientacni": "1", "Psc": "128 00", "Obec": "Praha",
          "CastObce": "Nové Město"}
PRAGUE_REGISTERED = "Na Poříčním právu 376/1, Nové Město, 12800 Praha 2"
VILLAGE = {**EMPTY, "CastObce": "Lhotka", "CisloPopisne": "12", "Psc": "26301", "Obec": "Obořiště"}
REGISTRY_NUMBER = {**EMPTY, "CisloEvidencni": "12", "Psc": "27731", "Obec": "Lhotka"}
FORM = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root>
<d:Bydliste><d:Stat>CZ</d:Stat><d:Obec>Praha</d:Obec><d:CastObce>Nové Město</d:CastObce><d:Ulice>Na Poříčním právu</d:Ulice>
 <d:CisloPopisne>376</d:CisloPopisne><d:CisloOrientacni>1</d:CisloOrientacni><d:Psc>128 00</d:Psc><d:CisloEvidencni/><d:Ruian/></d:Bydliste>
<d:Sidlo><d:Stat/><d:Obec>Obořiště</d:Obec><d:CastObce>Lhotka</d:CastObce><d:Ulice/><d:CisloPopisne>12</d:CisloPopisne>
 <d:CisloOrientacni/><d:Psc>26301</d:Psc><d:__PomValidovanaAdresa/></d:Sidlo>
<d:Ciziny><d:Stat>SK</d:Stat><d:Obec>Bratislava</d:Obec><d:Ruian/></d:Ciziny>
<d:Overena><d:Obec>Brno</d:Obec><d:__ValidovanaAdresa>true</d:__ValidovanaAdresa><d:Ruian/></d:Overena>
<d:Prazdna><d:Obec/><d:Ruian/></d:Prazdna><d:a/>
</d:root></fm:instance><fm:bind Id="k" nodeset="/d:root"><fm:bind Id="a" nodeset="d:a"/></fm:bind></fm:model></fm:formparams>
<fo:inline form602ct="btn" form602bind="a" form602btnclick=%s><![CDATA[Zkontrolovat]]></fo:inline>
</fo:root></dsig:Object></dsig:Signature>"""
ASK_FOR_ADDRESSES = ("{ var p = new DataParam(); p.xslName = 'Validace_AdresaWS_XpathAdres';"
                     " if (GetDocument().GetFormData(p).data == '') GetDocument().GetElement().SetDataTo('a', 'zadna'); }")


class AddressesOfTheForm(unittest.TestCase):
    def test_the_form_gets_no_address_and_its_czech_ones_wait_for_the_check(self):
        doc = engine.open_document((FORM % quoteattr(ASK_FOR_ADDRESSES)).encode())
        result = form_scripts.run_button_script(doc, 0, "", [])
        self.assertEqual(result["values"], {"a": "zadna"})
        self.assertEqual(result["addresses"], [PRAGUE, VILLAGE])
        self.assertEqual(doc.addresses_to_check, [PRAGUE, VILLAGE])

    def test_a_script_that_asks_for_no_addresses_leaves_none_to_check(self):
        doc = engine.open_document((FORM % quoteattr("{ GetDocument().GetElement().SetDataTo('a', 'x'); }")).encode())
        self.assertNotIn("addresses", form_scripts.run_button_script(doc, 0, "", []))
        self.assertEqual(doc.addresses_to_check, [])


class WrittenAsTheRegisterWritesIt(unittest.TestCase):
    def test_an_address_is_searched_for_as_one_line(self):
        self.assertEqual(ruian.written(PRAGUE), "Na Poříčním právu 376/1, Nové Město, 12800 Praha")
        self.assertEqual(ruian.written(VILLAGE), "Lhotka 12, 26301 Obořiště")
        self.assertEqual(ruian.written(REGISTRY_NUMBER), "č.ev. 12, 27731 Lhotka")
        self.assertEqual(ruian.written(PRAGUE, with_post_office=False), "Na Poříčním právu 376/1, Praha")

    def test_the_register_agrees_only_when_every_part_does(self):
        self.assertTrue(ruian.agrees(PRAGUE, PRAGUE_REGISTERED))
        self.assertTrue(ruian.agrees({**PRAGUE, "Obec": "Praha 2", "Ulice": "na poříčním  právu"}, PRAGUE_REGISTERED))
        self.assertTrue(ruian.agrees(VILLAGE, "Lhotka 12, 26301 Obořiště"))
        self.assertTrue(ruian.agrees(REGISTRY_NUMBER, "č.ev. 12, 27731 Lhotka"))
        for different in ({"Psc": "11000"}, {"CisloOrientacni": "2"}, {"CisloPopisne": "377"}, {"Obec": "Prahy"},
                          {"Ulice": "Na Poricnim pravu"}, {"CastObce": "Staré Město"}):
            with self.subTest(different):
                self.assertFalse(ruian.agrees({**PRAGUE, **different}, PRAGUE_REGISTERED))
        self.assertFalse(ruian.agrees(REGISTRY_NUMBER, "Lhotka 12, 27731 Lhotka"))
        self.assertFalse(ruian.agrees(PRAGUE, "Hlavní město Praha"))


class CheckedInTheRegister(unittest.TestCase):
    def check(self, *answers, address=PRAGUE):
        with mock.patch.object(ruian, "_candidates", side_effect=answers) as candidates:
            return ruian.check(address), [call.args[0] for call in candidates.call_args_list]

    def test_a_known_address_is_known(self):
        self.assertEqual(self.check([PRAGUE_REGISTERED])[0], {"address": ruian.written(PRAGUE), "verdict": ruian.KNOWN,
                                                              "registered": [PRAGUE_REGISTERED]})

    def test_a_wrong_postcode_finds_the_address_the_register_knows(self):
        wrong_postcode = {**PRAGUE, "Psc": "11000"}
        result, asked = self.check([], [PRAGUE_REGISTERED], address=wrong_postcode)
        self.assertEqual((result["verdict"], result["registered"]), (ruian.SIMILAR, [PRAGUE_REGISTERED]))
        self.assertEqual(asked, [ruian.written(wrong_postcode), ruian.written(wrong_postcode, with_post_office=False)])

    def test_an_address_found_nowhere_is_unknown(self):
        self.assertEqual(self.check([], [])[0]["verdict"], ruian.UNKNOWN)


class Answer(io.BytesIO):
    def __init__(self, content_type, body):
        super().__init__(body)
        self.headers = mock.Mock(get_content_type=mock.Mock(return_value=content_type))


class TheSearchOfCuzk(unittest.TestCase):
    def ask(self, content_type, body):
        opener = mock.Mock(open=mock.Mock(return_value=Answer(content_type, body)))
        with mock.patch.dict(os.environ, {"ZFO_OFFLINE": ""}), mock.patch.object(public_services, "opener", return_value=opener):
            try:
                return ruian._candidates("Lhotka 12"), opener.open.call_args[0][0].full_url
            except OSError:
                return None, opener.open.call_args[0][0].full_url

    def test_only_the_address_goes_to_the_known_search(self):
        found, url = self.ask("application/json", b'{"candidates": [{"address": "Lhotka 12, 26301 Obo\\u0159i\\u0161t\\u011b",'
                                                  b' "attributes": {"Type": "AdresniMisto"}},'
                                                  b' {"address": "Lhotka", "attributes": {"Type": "CastObce"}}, 5]}')
        self.assertEqual(found, ["Lhotka 12, 26301 Obořiště"])
        address, query = url.split("?")
        self.assertEqual(address, ruian.SEARCH)
        self.assertEqual(urllib.parse.parse_qs(query), {"SingleLine": ["Lhotka 12"], "f": ["json"], "maxLocations": ["5"]})

    def test_a_page_that_is_not_the_answer_of_the_register_counts_as_unavailable(self):
        for content_type, body in (("text/html", b"<html>Prihlaste se k Wi-Fi</html>"), ("application/json", b"[]"),
                                   ("application/json", b"{\"candidates\": 5}"), ("application/json", b"nejson")):
            with self.subTest(body):
                self.assertIsNone(self.ask(content_type, body)[0])

    def test_without_the_internet_nothing_is_asked(self):
        with mock.patch.dict(os.environ, {"ZFO_OFFLINE": "1"}):
            self.assertRaises(OSError, ruian._candidates, "Lhotka 12")


if __name__ == "__main__":
    unittest.main()
