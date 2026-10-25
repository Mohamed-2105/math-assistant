"""Tests of the file reading, the photo dates and the timetable."""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import get_args
from unittest import mock

import settings
from schemas import CoursAutorises
from sources import extraire_contenu_fichier, extraire_date_photo, identifier_creneau, pdf_vers_markdown, tronquer


class TimetableTest(unittest.TestCase):
    def test_monday_morning_is_a_machine_learning_lesson(self):
        cours, categorie, session = identifier_creneau(datetime(2026, 10, 19, 10, 0))
        self.assertEqual((cours, categorie), ("Machine Learning", "Notes de cours"))
        self.assertEqual(session, "2026-10-19_Machine Learning_Notes de cours")

    def test_friday_afternoon_is_a_tutorial(self):
        # Regression: this slot was filed as "Notes de cours" by mistake in the first version.
        cours, categorie, _ = identifier_creneau(datetime(2026, 10, 23, 15, 0))
        self.assertEqual((cours, categorie), ("Statistics & Probability in R", "PC"))

    def test_outside_the_timetable_is_a_free_session_of_the_day(self):
        cours, categorie, session = identifier_creneau(datetime(2026, 10, 20, 11, 0))
        self.assertEqual((cours, categorie, session), (None, "Notes de cours", "2026-10-20_SessionLibre"))

    def test_every_course_of_the_timetable_is_an_allowed_course(self):
        for creneau in settings.PLANNING:
            self.assertIn(creneau["cours"], get_args(CoursAutorises))


class PhotoDateTest(unittest.TestCase):
    def test_the_date_comes_from_the_file_name(self):
        self.assertEqual(extraire_date_photo("/any/folder/2026-10-19_10-15-30.png"), datetime(2026, 10, 19, 10, 15, 30))

    def test_other_separators_between_the_time_parts_are_accepted(self):
        self.assertEqual(extraire_date_photo("IMG_2026-10-19_10.15.30.jpg"), datetime(2026, 10, 19, 10, 15, 30))

    def test_a_name_without_a_date_gives_none(self):
        self.assertIsNone(extraire_date_photo("photo_without_date.png"))

    def test_an_impossible_date_gives_none(self):
        self.assertIsNone(extraire_date_photo("2026-13-45_10-15-30.png"))

    def test_the_file_itself_is_never_read(self):
        # Regression: the date used to fall back on EXIF data, then on the modification time,
        # which could file a photo under the wrong course without any message.
        self.assertIsNone(extraire_date_photo("/this/path/does/not/exist/photo.jpg"))


class ReadingTest(unittest.TestCase):
    def ecrire(self, tmp, nom, texte):
        chemin = Path(tmp) / nom
        chemin.write_text(texte, encoding="utf-8")
        return str(chemin)

    def test_the_language_of_a_notebook_comes_from_its_metadata(self):
        carnet = {"metadata": {"language_info": {"name": "R"}}, "cells": [{"cell_type": "code", "source": ["x <- 1"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            texte = extraire_contenu_fichier(self.ecrire(tmp, "lab.ipynb", json.dumps(carnet)))
        self.assertIn("```r\nx <- 1\n```", texte)

    def test_a_broken_notebook_gives_an_empty_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(extraire_contenu_fichier(self.ecrire(tmp, "lab.ipynb", "{not json")), "")

    def test_the_yaml_header_of_an_rmd_file_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            texte = extraire_contenu_fichier(self.ecrire(tmp, "lab.rmd", "---\ntitle: Lab\n---\nQuestion 1.\n"))
        self.assertNotIn("title: Lab", texte)
        self.assertIn("Question 1.", texte)

    def test_a_long_text_is_cut_to_the_limit(self):
        self.assertEqual(tronquer("a" * 10, 4, "test"), "aaaa")
        self.assertEqual(tronquer("abc", 4, "test"), "abc")


class PdfReadingTest(unittest.TestCase):
    """pdf_vers_markdown with a fake pymupdf4llm that records how it is called."""

    class FauxPymupdf4llm:
        def __init__(self, accepte_use_ocr=True):
            self.appels = []
            self.accepte_use_ocr = accepte_use_ocr

        def to_markdown(self, chemin, **options):
            self.appels.append(options)
            if "use_ocr" in options and not self.accepte_use_ocr:
                raise TypeError("to_markdown() got an unexpected keyword argument 'use_ocr'")
            return "texte du pdf"

    def lire(self, faux):
        with mock.patch.dict(sys.modules, {"pymupdf4llm": faux}):
            return pdf_vers_markdown("cours.pdf")

    def test_ocr_is_switched_off_by_default(self):
        faux = self.FauxPymupdf4llm()
        self.assertEqual(self.lire(faux), "texte du pdf")
        self.assertEqual(faux.appels, [{"use_ocr": False}])

    def test_ocr_is_left_to_the_library_when_the_setting_asks_for_it(self):
        faux = self.FauxPymupdf4llm()
        with mock.patch.object(settings, "PDF_AVEC_OCR", True):
            self.lire(faux)
        self.assertEqual(faux.appels, [{}])

    def test_a_version_of_the_library_without_the_option_is_called_without_it(self):
        faux = self.FauxPymupdf4llm(accepte_use_ocr=False)
        self.assertEqual(self.lire(faux), "texte du pdf")
        self.assertEqual(faux.appels, [{"use_ocr": False}, {}])

    def test_ocr_is_off_in_the_default_settings(self):
        self.assertFalse(settings.PDF_AVEC_OCR)


if __name__ == "__main__":
    unittest.main()
