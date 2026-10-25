"""Tests of the settings that are read from the environment."""
import os
import unittest
from unittest import mock

import settings


class ThinkingSettingsTest(unittest.TestCase):
    def lire(self, fonction, valeur):
        environnement = {} if valeur is None else {"REGLAGE": valeur}
        with mock.patch.dict(os.environ, environnement, clear=False):
            if valeur is None:
                os.environ.pop("REGLAGE", None)
            return fonction("REGLAGE")

    def test_an_empty_integer_setting_is_none(self):
        for valeur in (None, "", "   "):
            with self.subTest(value=valeur):
                self.assertIsNone(self.lire(settings._entier_optionnel, valeur))

    def test_an_integer_setting_is_read_as_a_number(self):
        self.assertEqual(self.lire(settings._entier_optionnel, "8192"), 8192)
        self.assertEqual(self.lire(settings._entier_optionnel, " -1 "), -1)

    def test_zero_is_a_value_and_not_an_empty_setting(self):
        self.assertEqual(self.lire(settings._entier_optionnel, "0"), 0)

    def test_a_text_instead_of_a_number_stops_with_a_clear_message(self):
        with self.assertRaises(ValueError) as contexte:
            self.lire(settings._entier_optionnel, "beaucoup")
        self.assertIn("REGLAGE doit être un nombre entier", str(contexte.exception))
        self.assertIn("'beaucoup'", str(contexte.exception))

    def test_an_empty_level_is_none(self):
        for valeur in (None, "", "  "):
            with self.subTest(value=valeur):
                self.assertIsNone(self.lire(settings._niveau_optionnel, valeur))

    def test_a_level_is_read_whatever_its_capitals(self):
        self.assertEqual(self.lire(settings._niveau_optionnel, "High"), "high")
        self.assertEqual(self.lire(settings._niveau_optionnel, " MEDIUM "), "medium")

    def test_every_known_level_is_accepted(self):
        for niveau in settings.NIVEAUX_REFLEXION:
            self.assertEqual(self.lire(settings._niveau_optionnel, niveau), niveau)

    def test_an_unknown_level_stops_with_the_list_of_the_levels(self):
        with self.assertRaises(ValueError) as contexte:
            self.lire(settings._niveau_optionnel, "enormous")
        message = str(contexte.exception)
        self.assertIn("REGLAGE doit valoir l'un de", message)
        for niveau in settings.NIVEAUX_REFLEXION:
            self.assertIn(niveau, message)

    def test_the_thinking_settings_are_off_by_default(self):
        # In a fresh environment nothing is asked of the models beyond the summary of their thoughts.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NIVEAU_REFLEXION", None)
            os.environ.pop("BUDGET_REFLEXION", None)
            self.assertIsNone(settings._niveau_optionnel("NIVEAU_REFLEXION"))
            self.assertIsNone(settings._entier_optionnel("BUDGET_REFLEXION"))


if __name__ == "__main__":
    unittest.main()
