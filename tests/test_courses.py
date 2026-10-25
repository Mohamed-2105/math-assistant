"""Tests of the choice of the course (the "Cours" column of Notion)."""
import unittest
from typing import get_args

import prompts
import settings
from schemas import (
    CONSIGNE_COURS,
    CoursAutorises,
    FicheCours,
    FicheExercices,
    FicheLab,
    FicheNotesManuscrites,
)


class CourseDefinitionTest(unittest.TestCase):
    def test_the_allowed_courses_are_exactly_the_courses_of_the_settings(self):
        self.assertEqual(get_args(CoursAutorises), tuple(settings.COURS))

    def test_every_course_has_a_description(self):
        for nom, description in settings.COURS.items():
            with self.subTest(course=nom):
                self.assertTrue(description.strip())

    def test_every_course_of_the_timetable_is_a_known_course(self):
        for creneau in settings.PLANNING:
            self.assertIn(creneau["cours"], settings.COURS)

    def test_the_instruction_lists_every_course_with_its_description(self):
        for nom, description in settings.COURS.items():
            self.assertIn(f'"{nom}" : {description}', CONSIGNE_COURS)


class CourseInstructionTest(unittest.TestCase):
    def test_the_four_schemas_give_the_same_instruction_for_the_course(self):
        champs = [FicheCours, FicheExercices, FicheLab, FicheNotesManuscrites]
        for schema in champs:
            with self.subTest(schema=schema.__name__):
                self.assertEqual(schema.model_fields["nom_du_cours_notion"].description, CONSIGNE_COURS)

    def test_the_prompts_that_choose_a_course_carry_the_instruction(self):
        # Regression: the exercise prompt used to give no hint at all about the courses.
        demandes = {
            "course": prompts.prompt_cours("x"),
            "exercises": prompts.prompt_exercices("x", "y"),
            "lab": prompts.prompt_lab("x", "y"),
            "notes outside the timetable": prompts.prompt_session(None, ""),
        }
        for nom, prompt in demandes.items():
            with self.subTest(prompt=nom):
                self.assertIn(CONSIGNE_COURS, prompt)

    def test_a_note_in_a_known_slot_is_told_its_course_and_gets_no_list(self):
        prompt = prompts.prompt_session("Machine Learning", "")
        self.assertIn("identifié avec certitude comme étant : 'Machine Learning'", prompt)
        self.assertNotIn(CONSIGNE_COURS, prompt)

    def test_the_refreshers_are_reserved_for_documents_without_application(self):
        for nom in ("Probability Refresher", "Linear Algebra Refresher"):
            with self.subTest(course=nom):
                self.assertIn("without application", settings.COURS[nom])
                self.assertIn("choose the main course", settings.COURS[nom])


if __name__ == "__main__":
    unittest.main()
