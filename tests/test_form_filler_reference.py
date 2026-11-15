"""Blanketa calculates public forms as Software602 Form Filler 4.81 does.

samples/form-filler holds the data Form Filler stored after it opened a public sample with the inputs below and calculated it.
"""
import copy
import unittest
import zipfile

from lxml import etree
from support import ROOT

import app
from zfoengine import api as engine

FORM_FILLER_DATA = ROOT / "samples/form-filler"
DAY_FORM_FILLER_CALCULATED_THE_SUBSIDY_FORMS = "14.11.2026"

CADASTRE_ON_THE_SCREEN = {
    "/d:root/d:tisk": "0",
    "//d:prepinac1a": "1",
    "//d:prepinac2a": "1",
    "//d:prepinac2b": "1",
    "//d:s2a_typ": "budova",
    "//d:s2a_parcelni_cislo2": "45/1",
    "//d:sekce2a/d:s2a_vlastnickeho_prava": "1",
    "//d:s2a_textarea1": "Zkušební text",
    "//d:o3_tel_mail": "2",
    "//d:s2b_vlastnickeho_prava": "2",
    "//d:s2b_textarea1": "Zkušební text",
    "//d:s2b_cp_cev2": "12",
    "//d:s2b_cast_obce": "Zkušební",
    "//d:s2b_katastralni_uzemi11": "Zkušební",
}
CADASTRE_PRINTED_WITH_A_PLOT_OF_SECTION_B = {
    "/d:root/d:tisk": "1",
    "//d:prepinac2a": "1",
    "//d:s2b_parcelni_cislo21": "123",
    "//d:o3_tel_mail": "1",
    "//d:o3_email": "nikdo@example.cz",
}
CADASTRE_PRINTED_WITH_A_BUILDING_OF_SECTION_A = {
    "/d:root/d:tisk": "1",
    "//d:s2a_typ": "budova",
    "//d:s2a_parcelni_cislo2": "45/1",
}

SUBSIDY_OF_AN_ENTREPRENEUR = {
    "/d:root/d:prohlaseni": DAY_FORM_FILLER_CALCULATED_THE_SUBSIDY_FORMS,
    "/d:root/d:nazev_programu": "Zkušební program",
    "/d:root/d:PID": "ABC123",
    "//d:zadatel/d:pravni_statut": "102",
    "//d:d_celkem": "30000",
    "//d:d_sloupec1": "10000",
    "//d:d_sloupec2": "20000",
    "//d:z_celkem": "70000",
    "//d:z_sloupec1": "30000",
    "//d:z_sloupec2": "40000",
    "//d:zdroje/d:zdroje_jine": "5000",
    "//d:mat_poz_knihy": "1500",
    "//d:mat_poz_material": "250.5",
    "//d:sl_vl_tisk": "100",
    "//d:sl_vl_posta": "20",
    "//d:vl_osobni_naklady": "1000",
}
SUBSIDY_OF_A_PERSON = {
    "/d:root/d:prohlaseni": DAY_FORM_FILLER_CALCULATED_THE_SUBSIDY_FORMS,
    "/d:root/@rok": "2026",
    "//d:zadatel/d:pravni_statut": "001",
    "//d:d_celkem": "10000",
    "//d:z_celkem": "20000",
}
SUBSIDY_WITH_VALUES_THE_FORM_KEEPS = {
    "/d:root/d:prohlaseni": DAY_FORM_FILLER_CALCULATED_THE_SUBSIDY_FORMS,
    "//d:zadatel/d:pravni_statut": "102",
    "//d:jmeno_r": "Zkušební",
    "//d:platce_DPH": "1",
    "//d:d_celkem": "0",
    "//d:z_celkem": "0",
    "//d:podil_LK": "12.5",
}
SUBSIDY_WITH_A_PROGRAMME_THE_FORM_CLEARS = {
    "/d:root/d:prohlaseni": DAY_FORM_FILLER_CALCULATED_THE_SUBSIDY_FORMS,
    "/d:root/d:podprogram_podpory": "KUOLX00WFXB3#KUOLSF00ARDS#00029#",
    "//d:zadatel/d:pravni_statut": "001",
    "//d:jmeno_r": "Zkušební",
    "//d:platce_DPH": "1",
    "//d:d_celkem": "30000",
    "//d:zdroje/d:zdroje_jine": "5000",
    "//d:z_sloupec1": "700",
}
CONTROL_SUM_OF_THE_SUBSIDY_FORM = "/d:root/@hash"
TOTALS_OVER_NUMBERS_NOBODY_WROTE = tuple(f"/d:root/d:oddil_5/d:env_vych/{total}" for total in (
    "d:env_poz_celkem", "d:env_vl_celkem", "d:material/d:mat_poz_celkem", "d:material/d:mat_vl_celkem", "d:sluzby/d:sl_poz_celkem",
    "d:sluzby/d:sl_vl_celkem"))

EMPLOYEE_WITH_A_BIRTH_NUMBER_AND_SYMBOLS = {
    "//d:client/@bno": "8558150007",
    "//d:employee/@dep": "112",
    "//d:employee/@fro": "2026-01-15",
    "//d:employee/@dat": "2026-01-10",
    "//d:comp/@vs": "1234567890",
}


def values_of(data):
    tree = data.getroottree()
    values = {}
    for element in tree.iter():
        path = tree.getpath(element)
        if len(element) == 0:
            values[path] = element.text or ""
        for name, value in element.attrib.items():
            values[f"{path}/@{name}"] = value
    return values


def form_filled_with(sample, inputs):
    published = zipfile.ZipFile(ROOT / "samples" / sample / "form.zfo").read("form.fo")
    doc = engine.open_document(published)
    data = doc._get_data_root()
    for path, value in inputs.items():
        for found in data.xpath(path, namespaces={"d": data.getroot().nsmap["d"]}):
            if isinstance(found, etree._Element):
                found.text = value
            else:
                found.getparent().set(found.attrname, value)
    form = app.publisher_layer(app.parse_fo(published))
    etree.SubElement(form, f"{{{app.DS}}}Object").append(copy.deepcopy(engine.data_element(doc)))
    return etree.tostring(form)


def calculated_by_blanketa(sample, inputs):
    doc = engine.open_document(form_filled_with(sample, inputs))
    engine.handle(doc, "recalc_form", "")
    return doc


def stored_by_form_filler(scenario):
    return values_of(etree.parse(str(FORM_FILLER_DATA / f"{scenario}.xml")).getroot())


def without(values, paths):
    return {path: value for path, value in values.items() if path not in paths}


class FormAgreesWithFormFiller(unittest.TestCase):
    sample = None

    def agrees(self, scenario, inputs, apart_from=()):
        doc = calculated_by_blanketa(self.sample, inputs)
        self.assertEqual(without(values_of(doc._get_data_root().getroot()), apart_from), without(stored_by_form_filler(scenario), apart_from))
        self.assertEqual(doc.script_problems, set())


class CadastreForm(FormAgreesWithFormFiller):
    """Its scripts answer by the field tisk, which the form sets to 1 only while it prints."""
    sample = "cuzk"

    def test_on_the_screen_no_part_counts_as_left_empty(self):
        self.agrees("cuzk-on-the-screen", CADASTRE_ON_THE_SCREEN)

    def test_printed_with_a_plot_of_section_b_only(self):
        self.agrees("cuzk-printed-with-a-plot-of-section-b", CADASTRE_PRINTED_WITH_A_PLOT_OF_SECTION_B)

    def test_printed_with_a_building_of_section_a_only(self):
        self.agrees("cuzk-printed-with-a-building-of-section-a", CADASTRE_PRINTED_WITH_A_BUILDING_OF_SECTION_A)


class SubsidyFormOfTheOlomoucRegion(FormAgreesWithFormFiller):
    """Blanketa does not calculate its control sum yet (arosis/blanketa#160). Where Form Filler stores NaN for a total over
    numbers nobody wrote, Blanketa counts them as zeros."""
    sample = "olk-dotace"
    differs = (CONTROL_SUM_OF_THE_SUBSIDY_FORM, *TOTALS_OVER_NUMBERS_NOBODY_WROTE)

    def test_shares_of_an_entrepreneur(self):
        self.agrees("olk-shares-of-an-entrepreneur", SUBSIDY_OF_AN_ENTREPRENEUR, apart_from=self.differs)

    def test_shares_of_a_person_in_another_year(self):
        self.agrees("olk-shares-of-a-person", SUBSIDY_OF_A_PERSON, apart_from=self.differs)

    def test_values_the_form_stops_calculating_stay(self):
        self.agrees("olk-values-the-form-keeps", SUBSIDY_WITH_VALUES_THE_FORM_KEEPS, apart_from=self.differs)

    def test_a_programme_that_does_not_fit_is_cleared_before_the_binds_that_stop_by_it(self):
        self.agrees("olk-programme-the-form-clears", SUBSIDY_WITH_A_PROGRAMME_THE_FORM_CLEARS, apart_from=self.differs)

    def test_form_filler_stores_no_number_for_totals_over_numbers_nobody_wrote(self):
        stored = stored_by_form_filler("olk-shares-of-an-entrepreneur")
        self.assertEqual({stored[total] for total in TOTALS_OVER_NUMBERS_NOBODY_WROTE}, {"NaN"})


class EmploymentNoticeOfTheSocialSecurity(FormAgreesWithFormFiller):
    sample = "cssz-onz"

    def test_birth_number_and_symbols(self):
        self.agrees("cssz-birth-number-and-symbols", EMPLOYEE_WITH_A_BIRTH_NUMBER_AND_SYMBOLS)


if __name__ == "__main__":
    unittest.main()
