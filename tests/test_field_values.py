import datetime
import unittest

import support  # noqa: F401

from zfoengine.field_values import FieldRules, format_date, value_problem


class FieldValues(unittest.TestCase):
    """The values a field accepts, as Form Filler's Checker.Check judges them."""

    def test_an_empty_value_is_left_to_the_required_check(self):
        self.assertIsNone(value_problem("", FieldRules("dat", "[0-9]", "1", "xs:int", "x")))

    def test_a_value_longer_than_the_field_allows(self):
        self.assertEqual(value_problem("12345", FieldRules(max_characters="4")), "Hodnota může mít nejvýše 4 znaky.")
        self.assertEqual(value_problem("12", FieldRules(max_characters="1")), "Hodnota může mít nejvýše 1 znak.")
        self.assertIsNone(value_problem("1234", FieldRules(max_characters="4")))

    def test_dates_times_and_numbers_are_checked_as_stored(self):
        self.assertIsNone(value_problem("1990-02-01", FieldRules("dat")))
        self.assertEqual(value_problem("1.2.1990", FieldRules("dat")), "Hodnota není datum.")
        self.assertIsNone(value_problem("8:30", FieldRules("tim")))
        self.assertIsNone(value_problem("08:30:00", FieldRules("tim")))
        self.assertEqual(value_problem("25:00", FieldRules("tim")), "Hodnota není čas.")
        self.assertIsNone(value_problem("-1234.5", FieldRules("num")))
        self.assertEqual(value_problem("12,5x", FieldRules("num")), "Hodnota není číslo.")

    def test_the_data_type_of_the_bind(self):
        self.assertIsNone(value_problem("255", FieldRules(data_type="xs:unsignedByte")))
        self.assertEqual(value_problem("256", FieldRules(data_type="xs:unsignedByte")), value_problem("x", FieldRules(data_type="xs:int")))
        self.assertEqual(value_problem("ano", FieldRules(data_type="xs:boolean")), "Hodnota není ano, nebo ne.")
        self.assertIsNone(value_problem("cokoli", FieldRules(data_type="xs:string")))

    def test_a_pattern_is_looked_for_anywhere_in_the_value(self):
        rules = FieldRules(data_type="xs:string", pattern="[0-3][0-9][.][0-1][0-9][.][1-2][0-9]{3}")
        self.assertIsNone(value_problem("ke dni 10.11.2026", rules))
        self.assertEqual(value_problem("2026-11-10", rules), "Hodnota nemá tvar, který formulář vyžaduje.")

    def test_characters_the_field_does_not_allow_are_named(self):
        self.assertEqual(value_problem("12a3b", FieldRules(allowed_characters="[0123456789]")),
                         "Hodnota obsahuje znaky, které pole nepřipouští: „a“, „b“.")
        self.assertIsNone(value_problem("ŠÍPEK", FieldRules(allowed_characters="uppercase")))
        self.assertIsNotNone(value_problem("Kůň", FieldRules(allowed_characters="uppercase|lowercase")))
        self.assertIsNone(value_problem("1.5", FieldRules(allowed_characters="decimal_digits")))

    def test_a_number_may_hold_the_comma_its_field_allows(self):
        self.assertIsNone(value_problem("00.0", FieldRules("num", "[,0123456789]")))
        self.assertIsNotNone(value_problem("00.0", FieldRules("txt", "[,0123456789]")))

    def test_dates_are_written_by_windows_pictures(self):
        date = datetime.date(2026, 2, 5)
        self.assertEqual([format_date(date, picture) for picture in ("dd.MM.yyyy", "d. M. yyyy", "d.M.yy")],
                         ["05.02.2026", "5. 2. 2026", "5.2.26"])


if __name__ == "__main__":
    unittest.main()
