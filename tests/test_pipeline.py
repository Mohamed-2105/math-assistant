"""End to end tests: the whole pipeline on the invented sample, with fake Notion and Gemini."""
import unittest
from unittest import mock

import pipeline
import settings
from tests import fakes

LEFT_IN_PLACE = {
    "01_A_Traiter/Exercices/sheet2.pdf",                    # no correction yet
    "01_A_Traiter/Labs/lab2.ipynb",                         # no correction yet
    "01_A_Traiter/Notes_Manuscrites/photo_without_date.png",  # no date in the name
}


def run(notion=None, gemini=None, extra_files=()):
    """Run the pipeline on the sample. Returns (environment, printed text, files before, files after)."""
    with fakes.environment(notion, gemini) as env:
        fakes.build_sample(env.drive)
        for chemin in extra_files:
            (env.drive / chemin).write_bytes(b"extra")
        before = fakes.list_files(env.drive)
        printed = fakes.run_pipeline()
        after = fakes.list_files(env.drive)
    return env, printed, before, after


class NominalRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env, cls.printed, cls.before, cls.after = run()

    def test_one_page_per_document_is_created(self):
        # 1 course + 1 exercise sheet + 2 labs + 4 note sessions
        self.assertEqual(self.env.notion.pages_created, 8)

    def test_processed_files_are_archived_and_the_others_stay(self):
        todo = {f for f in self.after if f.startswith("01_A_Traiter/")}
        self.assertEqual(todo, LEFT_IN_PLACE)
        archives = {f.split("/", 1)[1] for f in self.after if f.startswith("02_Archives/")}
        self.assertEqual(len(archives), 12)
        for nom in ("chapter1.pdf", "sheet1.pdf", "sheet1_Correction.pdf", "lab1.ipynb", "lab3.rmd",
                    "2026-10-19_10-15-00.png", "2026-10-23_15-30-00.png"):
            self.assertIn(nom, archives)

    def test_the_lab_without_correction_is_waited_for(self):
        self.assertIn("Attente du corrigé pour le Lab 'lab2.ipynb'", self.printed)

    def test_a_photo_without_a_date_in_its_name_is_reported_and_left_in_place(self):
        self.assertIn("Date introuvable dans le nom de 'photo_without_date.png'", self.printed)
        self.assertNotIn("SessionLibre_", self.printed)

    def test_photos_of_the_same_slot_make_one_session(self):
        self.assertIn("Traitement de la session '2026-10-19_Machine Learning_Notes de cours' (2 fichier(s))", self.printed)
        self.assertIn("Traitement de la session '2026-10-20_SessionLibre' (1 fichier(s))", self.printed)

    def test_the_friday_afternoon_photo_is_filed_as_a_tutorial(self):
        self.assertIn("Traitement de la session '2026-10-23_Statistics & Probability in R_PC'", self.printed)

    def test_a_note_in_a_known_slot_is_tied_to_the_latest_course_sheet(self):
        prompts = [c["contents"][-1] for c in self.env.gemini.calls if c["schema"] == "FicheNotesManuscrites"]
        self.assertTrue(any("CADRE FORMEL DU COURS OFFICIEL" in p for p in prompts))
        self.assertTrue(any("Déduis le cours exact" in p for p in prompts))

    def test_the_course_of_the_slot_replaces_the_guess_of_the_model_only_when_the_slot_is_known(self):
        # The fake model always answers "Probability Refresher". Only the free session of Tuesday
        # has no known slot, so only that page keeps the guess.
        cours = [b["properties"]["Cours"]["select"]["name"] for b in self.env.notion.page_bodies]
        self.assertEqual(cours.count("Probability Refresher"), 1)

    def test_every_sheet_is_asked_in_json_with_a_zero_temperature(self):
        self.assertTrue(all(c["temperature"] == 0.0 for c in self.env.gemini.calls))

    def test_the_formulas_of_every_page_are_checked_before_it_is_sent(self):
        self.assertEqual(len(self.env.katex.calls), self.env.notion.pages_created)

    def test_every_note_gets_a_second_pass(self):
        notes = [c for c in self.env.gemini.calls if c["schema"] == "FicheNotesManuscrites"]
        self.assertEqual(len(notes), 8)  # 4 sessions, 2 passes each

    def test_the_photos_and_the_pdfs_are_uploaded_by_name(self):
        self.assertEqual(sorted(self.env.gemini.uploads), [
            "2026-10-19_10-15-00.png", "2026-10-19_10-40-00.png", "2026-10-19_15-00-00.png",
            "2026-10-20_11-00-00.png", "2026-10-23_15-30-00.png",
            "chapter1.pdf", "sheet1.pdf", "sheet1_Correction.pdf"])


class PdfSentToGeminiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env, cls.printed, cls.before, cls.after = run()

    def appel(self, schema, index=0):
        return [c for c in self.env.gemini.calls if c["schema"] == schema][index]["contents"]

    def test_the_course_pdf_is_sent_as_a_file_in_front_of_the_prompt(self):
        contenu = self.appel("FicheCours")
        self.assertEqual(contenu[:2], ["Cours (PDF joint) :", "<uploaded:chapter1.pdf>"])
        self.assertIn("joint en PDF ci-dessus", contenu[-1])
        self.assertNotIn("sample text", contenu[-1])  # the text of the PDF is not copied into the prompt

    def test_the_two_pdfs_of_an_exercise_sheet_are_sent_with_their_labels(self):
        contenu = self.appel("FicheExercices")
        self.assertEqual(contenu[:4], ["Énoncé (PDF joint) :", "<uploaded:sheet1.pdf>",
                                       "Corrigé (PDF joint) :", "<uploaded:sheet1_Correction.pdf>"])

    def test_notebooks_and_rmd_labs_stay_text(self):
        for contenu in (self.appel("FicheLab", 0), self.appel("FicheLab", 1)):
            self.assertEqual(len(contenu), 1)  # the prompt alone
            self.assertNotIn("joint en PDF", contenu[0])
        self.assertIn("Implement ridge regression", self.appel("FicheLab", 0)[0])

    def test_every_uploaded_file_is_deleted_after_use(self):
        envoyes = sorted(f"files/{nom}" for nom in self.env.gemini.uploads)
        self.assertEqual(sorted(self.env.gemini.deleted), envoyes)

    def test_nothing_is_read_with_pymupdf4llm_when_the_pdfs_are_sent(self):
        # The fake reader would have put the text of the PDFs in the prompts.
        for appel in self.env.gemini.calls:
            self.assertNotIn("(sample text)", appel["contents"][-1])


class PdfAsTextTest(unittest.TestCase):
    def test_with_the_setting_off_the_pdf_is_read_as_text_and_not_uploaded(self):
        with mock.patch.object(settings, "PDF_VIA_GEMINI", False):
            env, printed, before, after = run()
        self.assertEqual(sorted(env.gemini.uploads), [
            "2026-10-19_10-15-00.png", "2026-10-19_10-40-00.png", "2026-10-19_15-00-00.png",
            "2026-10-20_11-00-00.png", "2026-10-23_15-30-00.png"])
        cours = [c for c in env.gemini.calls if c["schema"] == "FicheCours"][0]["contents"]
        self.assertEqual(len(cours), 1)
        self.assertIn("Chapter 1: Ridge regression (sample text)", cours[0])
        self.assertEqual(env.notion.pages_created, 8)


class PdfInLabsTest(unittest.TestCase):
    def test_a_lab_with_a_pdf_correction_sends_the_pdf_and_skips_the_code_comparison(self):
        with fakes.environment() as env:
            fakes.build_sample(env.drive)
            labs = env.drive / "01_A_Traiter"
            (labs / "Labs" / "lab4.ipynb").write_text('{"metadata": {}, "cells": [{"cell_type": "markdown", "source": ["Question 1. Fit a tree."]}]}', encoding="utf-8")
            (labs / "Labs_Corriges" / "lab4_Correction.pdf").write_bytes(b"%PDF fake")
            printed = fakes.run_pipeline()
        appels = [c["contents"] for c in env.gemini.calls if c["schema"] == "FicheLab"]
        avec_pdf = [c for c in appels if "<uploaded:lab4_Correction.pdf>" in c]
        self.assertEqual(len(avec_pdf), 1)
        self.assertEqual(avec_pdf[0][:2], ["Corrigé du lab (PDF joint) :", "<uploaded:lab4_Correction.pdf>"])
        self.assertIn("Fit a tree", avec_pdf[0][-1])
        self.assertIn("Contrôle de fidélité du code ignoré", printed)
        self.assertIn("files/lab4_Correction.pdf", env.gemini.deleted)


class CleanUpTest(unittest.TestCase):
    def test_uploaded_files_are_deleted_even_when_the_generation_fails(self):
        env, printed, before, after = run(gemini=fakes.FakeGemini(failing_models={"fake-model-a", "fake-model-b"}))
        self.assertTrue(env.gemini.uploads)
        self.assertEqual(sorted(env.gemini.deleted), sorted(f"files/{n}" for n in env.gemini.uploads))
        self.assertEqual(after, before)

    def test_a_file_that_cannot_be_deleted_is_reported_and_does_not_stop_the_run(self):
        env, printed, before, after = run(gemini=fakes.FakeGemini(delete_fails=True))
        self.assertIn("n'a pas pu être supprimé chez Gemini", printed)
        self.assertIn("Il expirera de lui-même", printed)
        self.assertEqual(env.notion.pages_created, 8)


class AudioTest(unittest.TestCase):
    def test_an_audio_file_in_the_notes_folder_is_ignored(self):
        # Audio is not supported: the file stays where it is and no page is made for it.
        env, printed, before, after = run(extra_files=["01_A_Traiter/Notes_Manuscrites/lecture.m4a"])
        self.assertIn("01_A_Traiter/Notes_Manuscrites/lecture.m4a", after)
        self.assertEqual(env.notion.pages_created, 8)
        self.assertNotIn("lecture.m4a", printed)


class TitleLimitTest(unittest.TestCase):
    def test_every_page_created_respects_the_title_limit(self):
        with fakes.environment() as env:
            fakes.build_sample(env.drive)
            with mock.patch.object(settings, "LIMITE_TITRE", 12):
                fakes.run_pipeline()
        titres = [b["properties"]["Name"]["title"][0]["text"]["content"] for b in env.notion.page_bodies]
        self.assertEqual(len(titres), 8)
        self.assertTrue(all(len(t) <= 12 for t in titres), titres)


class RawLatexInThePageTest(unittest.TestCase):
    """The case seen in Notion: the statements of a course written as bare formulas, without dollar signs."""

    def lancer(self):
        enonces = fakes.ENONCES_LATEX_BRUT

        def reponses(modele, schema, contenu):
            if schema != "FicheCours":
                return fakes.ANSWERS[schema]()
            fiche = fakes.cours_answer()
            elements = [e for partie in fiche["parties"] for e in partie["elements"]]
            for element, enonce in zip(elements, enonces):
                element["enonce_latex"] = enonce
            return fiche

        return run(gemini=fakes.FakeGemini(answers=reponses))

    def page_du_cours(self, env):
        return next(b for b in env.notion.page_bodies if b["properties"]["Catégorie"]["select"]["name"] == "Cours fiché")

    def test_every_bare_statement_is_an_equation_in_the_page(self):
        env, _, _, _ = self.lancer()
        paragraphes = [b["paragraph"]["rich_text"] for b in self.page_du_cours(env)["children"] if b["type"] == "paragraph"]
        for enonce in fakes.ENONCES_LATEX_BRUT:
            with self.subTest(statement=enonce[:30]):
                equations = [r for r in paragraphes if len(r) == 1 and r[0]["type"] == "equation" and r[0]["equation"]["expression"] == enonce]
                self.assertEqual(len(equations), 1)

    def test_no_raw_latex_is_left_as_plain_text_in_the_page(self):
        env, _, _, _ = self.lancer()
        for bloc in self.page_du_cours(env)["children"]:
            if bloc["type"] == "paragraph":
                for item in bloc["paragraph"]["rich_text"]:
                    if item["type"] == "text":
                        self.assertNotIn("\\widehat", item["text"]["content"])
                        self.assertNotIn("\\top", item["text"]["content"])

    def test_the_repaired_formulas_are_checked_by_katex_like_the_others(self):
        env, _, _, _ = self.lancer()
        verifiees = {expression for expression, _ in env.katex.calls[0]}
        for enonce in fakes.ENONCES_LATEX_BRUT:
            self.assertIn(enonce, verifiees)

    def test_the_warning_tells_that_the_model_did_not_follow_the_rule_and_that_the_page_was_repaired(self):
        _, imprime, _, _ = self.lancer()
        self.assertIn("extrait(s) de LaTeX hors de $...$", imprime)
        self.assertIn("Ils sont mis entre $ automatiquement", imprime)

    def test_the_course_is_processed_and_archived_all_the_same(self):
        env, _, _, apres = self.lancer()
        self.assertIn("02_Archives/chapter1.pdf", apres)
        self.assertEqual(env.notion.pages_created, 8)


class EmptySheetTest(unittest.TestCase):
    def test_a_course_that_gives_no_content_creates_no_page_and_stays_in_place(self):
        # Regression: this used to create an empty page and archive the PDF as a success.
        def reponses(modele, schema, contenu):
            return {**fakes.cours_answer(), "parties": []} if schema == "FicheCours" else fakes.ANSWERS[schema]()

        env, printed, before, after = run(gemini=fakes.FakeGemini(answers=reponses))
        self.assertIn("01_A_Traiter/Cours/chapter1.pdf", after)
        self.assertNotIn("02_Archives/chapter1.pdf", after)
        self.assertIn("Échec sur le cours 'chapter1.pdf'", printed)
        self.assertIn("fiche vide", printed)
        self.assertEqual(env.notion.pages_created, 7)

    def test_a_linter_that_empties_a_note_does_not_wipe_it(self):
        # The second pass used to be able to return an empty note, which then replaced the first one.
        def reponses(modele, schema, contenu):
            if "vérificateur syntaxique" in str(contenu):
                return {**fakes.notes_answer(), "sections": []}
            return fakes.ANSWERS[schema]()

        env, printed, before, after = run(gemini=fakes.FakeGemini(answers=reponses))
        self.assertIn("Avertissement Passe 2 (Linter)", printed)
        self.assertEqual(env.notion.pages_created, 8)
        notes = [b for b in env.notion.page_bodies if b["properties"].get("Catégorie", {}).get("select", {}).get("name") in ("Notes de cours", "PC")
                 and any(c["type"] == "heading_3" for c in b["children"])]
        self.assertTrue(notes)


class SlowGeminiTest(unittest.TestCase):
    def test_when_gemini_never_answers_in_time_every_file_stays_in_place_and_the_run_ends(self):
        env, printed, before, after = run(gemini=fakes.FakeGemini(timing_out_models={"fake-model-a", "fake-model-b"}))
        self.assertEqual(after, before)
        self.assertEqual(env.notion.pages_created, 0)
        self.assertIn("Échec sur le cours 'chapter1.pdf'", printed)
        self.assertIn("pas de réponse du modèle fake-model-b", printed)

    def test_a_slow_first_model_only_costs_one_call_per_sheet(self):
        env, printed, before, after = run(gemini=fakes.FakeGemini(timing_out_models={"fake-model-a"}))
        self.assertEqual(env.notion.pages_created, 8)
        models = [c["model"] for c in env.gemini.calls]
        self.assertEqual(models[:2], ["fake-model-a", "fake-model-b"])
        self.assertEqual(env.pauses, [])

    def test_an_upload_that_gets_no_answer_leaves_the_files_in_place(self):
        env, printed, before, after = run(gemini=fakes.FakeGemini(upload_times_out=True))
        self.assertEqual(env.notion.pages_created, 2)  # only the two labs, which are read as text and not uploaded
        self.assertIn("Échec sur le cours 'chapter1.pdf'", printed)
        self.assertIn("Échec sur la session", printed)
        self.assertIn("pendant l'envoi de", printed)
        notes = [f for f in after if f.startswith("01_A_Traiter/Notes_Manuscrites/")]
        self.assertEqual(len(notes), 6)  # 5 dated photos and the photo without a date


class FailureTest(unittest.TestCase):
    def test_when_notion_refuses_everything_the_files_stay_and_the_run_continues(self):
        env, printed, before, after = run(notion=fakes.FakeNotion(reject_all_posts=True))
        self.assertEqual(after, before)
        self.assertIn("Échec sur le cours 'chapter1.pdf'", printed)
        self.assertIn("Échec sur le Lab 'lab1.ipynb'", printed)
        self.assertIn("Échec sur la session", printed)

    def test_when_a_call_fails_the_same_model_is_tried_again(self):
        env, printed, before, after = run(gemini=fakes.FakeGemini(fail_first_calls=1))
        models = [c["model"] for c in env.gemini.calls]
        self.assertEqual(models[:3], ["fake-model-a", "fake-model-a", "fake-model-a"])
        self.assertEqual(env.notion.pages_created, 8)

    def test_when_a_model_is_down_every_sheet_goes_to_the_next_one(self):
        env, printed, before, after = run(gemini=fakes.FakeGemini(failing_models={"fake-model-a"}))
        models = [c["model"] for c in env.gemini.calls]
        self.assertEqual(models[:3], ["fake-model-a", "fake-model-a", "fake-model-b"])
        self.assertEqual(env.notion.pages_created, 8)

    def test_a_first_refusal_by_notion_is_absorbed(self):
        env, printed, before, after = run(notion=fakes.FakeNotion(reject_first_post=True))
        self.assertEqual(env.notion.pages_created, 8)

    def test_an_invalid_answer_from_a_model_counts_as_a_failure_of_that_model(self):
        # Regression: an answer that does not match the schema used to crash the whole run.
        class Broken(fakes.FakeGemini):
            def generate_content(self, model, contents, config):
                response = super().generate_content(model, contents, config)
                if len(self.calls) == 1:
                    response.parsed = None
                    response.text = "{truncated"
                return response

        env, printed, before, after = run(gemini=Broken())
        self.assertEqual([c["model"] for c in env.gemini.calls][:2], ["fake-model-a", "fake-model-a"])
        self.assertEqual(env.notion.pages_created, 8)

    def test_the_linter_failure_keeps_the_first_version_of_a_note(self):
        def flaky(contents, schema):
            if "vérificateur syntaxique" in str(contents):
                raise RuntimeError("linter down (fake)")
            return schema.model_validate(fakes.ANSWERS[schema.__name__]())

        with fakes.environment() as env:
            fakes.build_sample(env.drive)
            with mock.patch.object(pipeline, "generer_fiche", flaky):
                printed = fakes.run_pipeline()
        self.assertIn("Avertissement Passe 2 (Linter)", printed)
        self.assertEqual(env.notion.pages_created, 8)


if __name__ == "__main__":
    unittest.main()
