"""Tests of the diagnostic of the Gemini models."""
import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import diagnostic_gemini
import settings
from tests import fakes


def lancer(gemini, **arguments):
    """Run the diagnostic with a fake Gemini. Returns (environment, printed text)."""
    sortie = io.StringIO()
    with fakes.environment(gemini=gemini) as env:
        with contextlib.redirect_stdout(sortie):
            diagnostic_gemini.diagnostiquer(arguments.pop("modeles", ["fake-model-a"]), **arguments)
    return env, sortie.getvalue()


class SummaryTest(unittest.TestCase):
    def test_a_model_that_sends_thoughts_is_reported_as_such(self):
        lignes = diagnostic_gemini.resumer([(1.0, "pensées", 40), (2.5, "pensées", 60), (9.0, "réponse", 100)])
        self.assertEqual(lignes[0], "envoie des résumés de pensées : 2 morceau(x), le premier à 1.0 s")
        self.assertEqual(lignes[1], "premier morceau de réponse à 9.0 s")

    def test_a_model_that_sends_none_is_reported_with_a_reminder_that_it_may_still_think(self):
        lignes = diagnostic_gemini.resumer([(30.0, "réponse", 100)])
        self.assertIn("n'envoie aucun résumé de pensées", lignes[0])
        self.assertIn("réfléchir sans le dire", lignes[0])

    def test_no_answer_is_reported(self):
        self.assertIn("aucune réponse reçue", diagnostic_gemini.resumer([])[1])

    def test_the_token_counts_announced_by_gemini_are_shown(self):
        usage = type("Usage", (), {"thoughts_token_count": 640, "prompt_token_count": 41000})()
        lignes = diagnostic_gemini.resumer([(1.0, "réponse", 10)], usage)
        self.assertIn("jetons de réflexion annoncés par Gemini : 640", lignes)
        self.assertIn("jetons envoyés à Gemini : 41000", lignes)


class SmallQuestionTest(unittest.TestCase):
    def test_the_settings_in_use_are_printed_first(self):
        _, affichage = lancer(fakes.FakeGemini())
        premiere = affichage.splitlines()[0]
        self.assertIn(f"RESUME_PENSEES={settings.RESUME_PENSEES}", premiere)
        self.assertIn(f"DELAI_APPEL_GEMINI={settings.DELAI_APPEL_GEMINI} s", premiere)

    def test_a_model_that_sends_thoughts_is_seen_to_send_them(self):
        env, affichage = lancer(fakes.FakeGemini(thinking_pieces=2))
        self.assertIn("envoie des résumés de pensées : 2 morceau(x)", affichage)
        self.assertIn("premier morceau de pensées", affichage)
        self.assertTrue(env.gemini.calls[0]["thinking"].include_thoughts)

    def test_a_model_that_sends_none_is_seen_not_to_send_any(self):
        _, affichage = lancer(fakes.FakeGemini(thinking_pieces=0))
        self.assertIn("n'envoie aucun résumé de pensées", affichage)
        self.assertIn("premier morceau de réponse", affichage)

    def test_the_thoughts_are_not_asked_for_when_the_setting_is_off(self):
        with mock.patch.object(settings, "RESUME_PENSEES", False):
            env, affichage = lancer(fakes.FakeGemini(thinking_pieces=2))
        self.assertIsNone(env.gemini.calls[0]["thinking"])
        self.assertIn("n'envoie aucun résumé de pensées", affichage)

    def test_every_model_is_tested(self):
        env, affichage = lancer(fakes.FakeGemini(), modeles=["fake-model-a", "fake-model-b"])
        self.assertEqual([c["model"] for c in env.gemini.calls], ["fake-model-a", "fake-model-b"])
        self.assertIn("=== fake-model-a ===", affichage)
        self.assertIn("=== fake-model-b ===", affichage)

    def test_a_model_that_refuses_the_option_is_named(self):
        _, affichage = lancer(fakes.FakeGemini(thinking_error_models={"fake-model-a"}))
        self.assertIn("refuse les réglages de réflexion", affichage)

    def test_a_model_that_gets_no_answer_in_time_is_named(self):
        _, affichage = lancer(fakes.FakeGemini(timing_out_models={"fake-model-a"}))
        self.assertIn(f"plus rien reçu pendant {settings.DELAI_APPEL_GEMINI} s", affichage)

    def test_another_error_is_printed_and_the_next_model_is_still_tested(self):
        env, affichage = lancer(fakes.FakeGemini(failing_models={"fake-model-a"}), modeles=["fake-model-a", "fake-model-b"])
        self.assertIn("erreur : 503 UNAVAILABLE (fake)", affichage)
        self.assertIn("=== fake-model-b ===", affichage)
        self.assertEqual(len(env.gemini.calls), 2)

    def test_the_question_is_free_text_without_a_schema(self):
        env, _ = lancer(fakes.FakeGemini())
        self.assertIsNone(env.gemini.calls[0]["schema"])
        self.assertEqual(env.gemini.calls[0]["contents"], [diagnostic_gemini.QUESTION])


class ThinkingSettingsInTheDiagnosticTest(unittest.TestCase):
    def test_the_thinking_settings_in_use_are_printed(self):
        with mock.patch.multiple(settings, NIVEAU_REFLEXION="high", BUDGET_REFLEXION=None):
            _, affichage = lancer(fakes.FakeGemini())
        premiere = affichage.splitlines()[0]
        self.assertIn("NIVEAU_REFLEXION=high", premiere)
        self.assertIn("BUDGET_REFLEXION=défaut du modèle", premiere)

    def test_a_budget_of_zero_is_printed_as_zero_and_not_as_the_default(self):
        with mock.patch.multiple(settings, BUDGET_REFLEXION=0):
            _, affichage = lancer(fakes.FakeGemini())
        self.assertIn("BUDGET_REFLEXION=0,", affichage.splitlines()[0])

    def test_the_diagnostic_asks_the_model_with_the_same_settings_as_the_program(self):
        with mock.patch.multiple(settings, NIVEAU_REFLEXION="low"):
            env, _ = lancer(fakes.FakeGemini())
        self.assertEqual(env.gemini.calls[0]["thinking"].thinking_level.name, "LOW")


class PdfTest(unittest.TestCase):
    def avec_un_pdf(self, gemini, modeles=("fake-model-a",)):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "cours.pdf")
            open(chemin, "wb").write(b"%PDF fake")
            return lancer(gemini, modeles=list(modeles), pdf=chemin)

    def test_the_request_is_the_real_one_of_a_course_sheet(self):
        env, _ = self.avec_un_pdf(fakes.FakeGemini())
        appel = env.gemini.calls[0]
        self.assertEqual(appel["schema"], "FicheCours")
        self.assertEqual(appel["contents"][:2], ["Cours (PDF joint) :", "<uploaded:cours.pdf>"])
        self.assertIn("joint en PDF ci-dessus", appel["contents"][-1])

    def test_the_file_is_uploaded_once_and_deleted_afterwards(self):
        env, _ = self.avec_un_pdf(fakes.FakeGemini(), modeles=["fake-model-a", "fake-model-b"])
        self.assertEqual(env.gemini.uploads, ["cours.pdf"])
        self.assertEqual(env.gemini.deleted, ["files/cours.pdf"])

    def test_the_reading_stops_at_the_first_piece_of_the_answer(self):
        def lire(arreter):
            with fakes.environment(gemini=fakes.FakeGemini(piece_size=10, thinking_pieces=2)):
                with contextlib.redirect_stdout(io.StringIO()):
                    evenements, _ = diagnostic_gemini.observer("fake-model-a", "question", {"thinking_config": object()}, arreter)
            return [e for e in evenements if e[1] == "réponse"]

        self.assertEqual(len(lire(True)), 1)
        self.assertGreater(len(lire(False)), 1)

    def test_the_thoughts_seen_before_the_answer_are_reported(self):
        _, affichage = self.avec_un_pdf(fakes.FakeGemini(piece_size=50, thinking_pieces=2))
        self.assertIn("premier morceau de pensées", affichage)
        self.assertIn("envoie des résumés de pensées : 2 morceau(x)", affichage)

    def test_the_file_is_deleted_even_when_a_model_fails(self):
        env, _ = self.avec_un_pdf(fakes.FakeGemini(failing_models={"fake-model-a"}))
        self.assertEqual(env.gemini.deleted, ["files/cours.pdf"])


class ListModelsTest(unittest.TestCase):
    def lister(self, filtre=None):
        sortie = io.StringIO()
        with fakes.environment() as env:
            with contextlib.redirect_stdout(sortie):
                diagnostic_gemini.lister(filtre)
        return env, sortie.getvalue()

    def test_only_the_models_that_write_text_are_listed(self):
        _, affichage = self.lister()
        self.assertIn("fake-model-a", affichage)
        self.assertIn("fake-pro", affichage)
        self.assertNotIn("fake-embedding", affichage)

    def test_the_names_are_given_as_they_must_be_written_in_the_settings(self):
        _, affichage = self.lister()
        self.assertNotIn("models/", affichage)

    def test_the_models_are_sorted_by_name(self):
        _, affichage = self.lister()
        ordre = [affichage.index(nom) for nom in ("fake-model-a", "fake-model-b", "fake-pro")]
        self.assertEqual(ordre, sorted(ordre))

    def test_the_models_that_are_already_in_the_settings_are_marked(self):
        _, affichage = self.lister()
        lignes = {ligne.split()[0]: ligne for ligne in affichage.splitlines() if ligne.startswith("fake-")}
        self.assertIn("<- dans GEMINI_MODELS", lignes["fake-model-a"])
        self.assertIn("<- dans GEMINI_MODELS", lignes["fake-model-b"])
        self.assertNotIn("<- dans GEMINI_MODELS", lignes["fake-pro"])

    def test_the_limits_and_the_thinking_are_shown(self):
        _, affichage = self.lister()
        self.assertIn("entrée max 1048576 jetons", affichage)
        self.assertIn("sortie max 65536 jetons", affichage)
        self.assertIn("réfléchit", affichage)
        self.assertIn("ne réfléchit pas", affichage)

    def test_a_missing_limit_is_simply_left_out(self):
        _, affichage = self.lister("pro")
        self.assertIn("entrée max 1048576 jetons", affichage)
        self.assertNotIn("sortie max", affichage)

    def test_a_word_keeps_only_the_names_that_contain_it(self):
        _, affichage = self.lister("pro")
        self.assertIn("fake-pro", affichage)
        self.assertNotIn("fake-model-a", affichage)
        self.assertIn("1 modèle(s)", affichage)

    def test_the_filter_ignores_capitals(self):
        _, affichage = self.lister("PRO")
        self.assertIn("fake-pro", affichage)

    def test_a_filter_that_matches_nothing_says_so(self):
        _, affichage = self.lister("zzz")
        self.assertIn("Aucun modèle trouvé avec « zzz » dans son nom.", affichage)

    def test_it_explains_where_to_write_the_name(self):
        _, affichage = self.lister()
        self.assertIn("GEMINI_MODELS du fichier .env", affichage)

    def test_the_list_does_not_ask_for_any_answer_from_a_model(self):
        env, _ = self.lister()
        self.assertEqual(env.gemini.calls, [])
        self.assertEqual(env.gemini.uploads, [])


class MainTest(unittest.TestCase):
    def appeler(self, arguments, cle="une-cle"):
        sortie = io.StringIO()
        with mock.patch.object(settings, "GEMINI_API_KEY", cle), mock.patch.object(diagnostic_gemini, "diagnostiquer") as diagnostiquer, \
                contextlib.redirect_stdout(sortie):
            code = diagnostic_gemini.main(arguments)
        return code, sortie.getvalue(), diagnostiquer

    def test_without_a_key_it_stops_with_a_message(self):
        code, affichage, diagnostiquer = self.appeler([], cle="")
        self.assertEqual(code, 1)
        self.assertIn("GEMINI_API_KEY est vide", affichage)
        diagnostiquer.assert_not_called()

    def test_a_missing_pdf_stops_with_a_message(self):
        code, affichage, diagnostiquer = self.appeler(["--pdf", "/no/such/file.pdf"])
        self.assertEqual(code, 1)
        self.assertIn("PDF introuvable", affichage)
        diagnostiquer.assert_not_called()

    def test_every_model_of_the_settings_is_tested_by_default(self):
        with mock.patch.object(settings, "GEMINI_MODELS", ["m1", "m2"]):
            code, _, diagnostiquer = self.appeler([])
        self.assertEqual(code, 0)
        diagnostiquer.assert_called_once_with(["m1", "m2"], None)

    def test_the_option_list_prints_the_models_and_stops_there(self):
        sortie = io.StringIO()
        with fakes.environment(), mock.patch.object(diagnostic_gemini, "diagnostiquer") as diagnostiquer, contextlib.redirect_stdout(sortie):
            code = diagnostic_gemini.main(["--lister", "--filtre", "pro"])
        self.assertEqual(code, 0)
        self.assertIn("fake-pro", sortie.getvalue())
        diagnostiquer.assert_not_called()

    def test_the_list_needs_the_key_too(self):
        code, affichage, _ = self.appeler(["--lister"], cle="")
        self.assertEqual(code, 1)
        self.assertIn("GEMINI_API_KEY est vide", affichage)

    def test_one_model_can_be_chosen(self):
        _, _, diagnostiquer = self.appeler(["--modele", "m3"])
        diagnostiquer.assert_called_once_with(["m3"], None)


if __name__ == "__main__":
    unittest.main()
