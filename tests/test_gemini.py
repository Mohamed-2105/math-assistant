"""Tests of the retries and of the fallback between Gemini models."""
import contextlib
import io
import itertools
import threading
import unittest
from unittest import mock

import httpx
from google.genai import errors

import gemini_client
import prompts
import settings
from schemas import (
    CONSIGNE_FORMULES,
    FicheCours,
    FicheExercices,
    FicheLab,
    FicheNotesManuscrites,
)
from tests import fakes


def demander(gemini):
    """Ask for a course sheet with the given fake Gemini. Returns (sheet or error, environment, printed text)."""
    sortie = io.StringIO()
    with fakes.environment(gemini=gemini) as env:
        with contextlib.redirect_stdout(sortie):
            try:
                resultat = gemini_client.generer_fiche("prompt", FicheCours)
            except RuntimeError as erreur:
                resultat = erreur
    return resultat, env, sortie.getvalue()


def modeles(env):
    return [appel["model"] for appel in env.gemini.calls]


class RetryTest(unittest.TestCase):
    def test_a_call_that_works_is_made_once_without_any_pause(self):
        fiche, env, _ = demander(fakes.FakeGemini())
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a"])
        self.assertEqual(env.pauses, [])

    def test_a_failed_call_is_tried_again_on_the_same_model_after_a_pause(self):
        fiche, env, affichage = demander(fakes.FakeGemini(fail_first_calls=1))
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-a"])
        self.assertEqual(env.pauses, [settings.PAUSE_ENTRE_ESSAIS])
        self.assertIn(f"Nouvel essai dans {settings.PAUSE_ENTRE_ESSAIS} s", affichage)
        self.assertIn("modèle : fake-model-a (essai 2/2)", affichage)

    def test_the_first_try_of_a_model_is_announced_without_a_counter(self):
        _, _, affichage = demander(fakes.FakeGemini())
        self.assertIn("Tentative de génération avec le modèle : fake-model-a...", affichage)
        self.assertNotIn("essai", affichage)

    def test_a_model_that_fails_twice_gives_way_to_the_next_one(self):
        fiche, env, affichage = demander(fakes.FakeGemini(failing_models={"fake-model-a"}))
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-a", "fake-model-b"])
        self.assertEqual(env.pauses, [settings.PAUSE_ENTRE_ESSAIS])
        self.assertIn("Essai du modèle suivant", affichage)

    def test_the_second_model_is_also_tried_twice(self):
        fiche, env, _ = demander(fakes.FakeGemini(fail_first_calls=3))
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-a", "fake-model-b", "fake-model-b"])
        self.assertEqual(len(env.pauses), 2)

    def test_when_every_try_fails_an_error_is_raised_after_two_tries_per_model(self):
        erreur, env, _ = demander(fakes.FakeGemini(failing_models={"fake-model-a", "fake-model-b"}))
        self.assertIsInstance(erreur, RuntimeError)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-a", "fake-model-b", "fake-model-b"])
        self.assertIn("Tous les modèles Gemini de fallback ont échoué", str(erreur))
        self.assertIn("503 UNAVAILABLE", str(erreur))

    def test_there_is_no_pause_after_the_last_try_of_a_model(self):
        _, env, _ = demander(fakes.FakeGemini(failing_models={"fake-model-a", "fake-model-b"}))
        self.assertEqual(len(env.pauses), 2)  # one between the two tries of each model, none after the last

    def test_the_number_of_tries_comes_from_the_settings(self):
        with mock.patch.object(settings, "TENTATIVES_PAR_MODELE", 1):
            fiche, env, _ = demander(fakes.FakeGemini(failing_models={"fake-model-a"}))
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-b"])
        self.assertEqual(env.pauses, [])

    def test_three_tries_per_model_can_be_asked(self):
        with mock.patch.object(settings, "TENTATIVES_PAR_MODELE", 3):
            _, env, _ = demander(fakes.FakeGemini(failing_models={"fake-model-a"}))
        self.assertEqual(modeles(env), ["fake-model-a"] * 3 + ["fake-model-b"])

    def test_the_pause_comes_from_the_settings(self):
        with mock.patch.object(settings, "PAUSE_ENTRE_ESSAIS", 7):
            _, env, _ = demander(fakes.FakeGemini(fail_first_calls=1))
        self.assertEqual(env.pauses, [7])

    def test_an_answer_that_does_not_match_the_schema_is_retried_too(self):
        class Tronquee(fakes.FakeGemini):
            def generate_content(self, model, contents, config):
                reponse = super().generate_content(model, contents, config)
                if len(self.calls) == 1:
                    reponse.parsed = None
                    reponse.text = "{truncated"
                return reponse

        fiche, env, _ = demander(Tronquee())
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-a"])


def sans_dollars():
    """A course answer whose formulas are written without dollar signs, like the page seen in Notion."""
    reponse = fakes.cours_answer()
    reponse["parties"][0]["elements"][0]["enonce_latex"] = "Let h_{ij} = (P_X)_{ij} with 0 \\leq h_{ii} \\leq 1 ."
    return reponse


def vide():
    return {**fakes.cours_answer(), "parties": []}


class TimeoutTest(unittest.TestCase):
    def test_a_model_that_does_not_answer_in_time_gives_way_at_once_to_the_next_one(self):
        fiche, env, affichage = demander(fakes.FakeGemini(timing_out_models={"fake-model-a"}))
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-b"])  # no second try: the model is slow
        self.assertEqual(env.pauses, [])
        self.assertIn(f"le modèle fake-model-a n'a pas répondu en {settings.DELAI_APPEL_GEMINI} s. Essai du modèle suivant", affichage)

    def test_the_last_model_does_not_announce_a_next_one(self):
        _, _, affichage = demander(fakes.FakeGemini(timing_out_models={"fake-model-a", "fake-model-b"}))
        derniere = [ligne for ligne in affichage.splitlines() if "fake-model-b n'a pas répondu" in ligne][0]
        self.assertNotIn("Essai du modèle suivant", derniere)

    def test_when_every_model_is_too_slow_the_work_stops_with_a_clear_message(self):
        erreur, env, _ = demander(fakes.FakeGemini(timing_out_models={"fake-model-a", "fake-model-b"}))
        self.assertIsInstance(erreur, RuntimeError)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-b"])
        self.assertIn(f"pas de réponse du modèle fake-model-b en {settings.DELAI_APPEL_GEMINI} s", str(erreur))

    def test_the_delay_in_the_message_comes_from_the_settings(self):
        with mock.patch.object(settings, "DELAI_APPEL_GEMINI", 45):
            erreur, _, affichage = demander(fakes.FakeGemini(timing_out_models={"fake-model-a", "fake-model-b"}))
        self.assertIn("en 45 s", affichage)
        self.assertIn("en 45 s", str(erreur))

    def test_a_builtin_timeout_error_is_handled_like_an_httpx_one(self):
        self.assertTrue(gemini_client.est_un_delai_depasse(TimeoutError("slow")))
        self.assertTrue(gemini_client.est_un_delai_depasse(httpx.ConnectTimeout("slow")))
        self.assertFalse(gemini_client.est_un_delai_depasse(RuntimeError("503")))

    def test_a_slow_call_after_a_fast_error_still_moves_on_to_the_next_model(self):
        class Mixte(fakes.FakeGemini):
            def generate_content(self, model, contents, config):
                if model == "fake-model-a" and len(self.calls) >= 1:
                    self.calls.append({"model": model, "schema": "x", "temperature": 0.0, "contents": []})
                    raise httpx.ReadTimeout("timed out")
                return super().generate_content(model, contents, config)

        fiche, env, _ = demander(Mixte(fail_first_calls=1))
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-a", "fake-model-b"])

    def test_the_client_is_created_with_the_delay_in_milliseconds(self):
        with mock.patch.object(gemini_client, "_client", None), \
                mock.patch.object(gemini_client.genai, "Client") as fabrique:
            gemini_client.get_client()
        options = fabrique.call_args.kwargs["http_options"]
        self.assertEqual(options.timeout, settings.DELAI_APPEL_GEMINI * 1000)

    def test_a_photo_upload_that_gets_no_answer_gives_a_clear_error(self):
        with fakes.environment(gemini=fakes.FakeGemini(upload_times_out=True)):
            with self.assertRaises(TimeoutError) as contexte:
                gemini_client.televerser_fichier("/some/folder/photo.png")
        self.assertIn("pendant l'envoi de '/some/folder/photo.png'", str(contexte.exception))

    def test_an_upload_error_that_is_not_a_delay_is_left_as_it_is(self):
        class Panne(fakes.FakeGemini):
            def upload(self, file):
                raise RuntimeError("disk full (fake)")

        with fakes.environment(gemini=Panne()):
            with self.assertRaises(RuntimeError):
                gemini_client.televerser_fichier("/some/folder/photo.png")


def horloge(pas):
    """A fake clock: every reading is `pas` seconds after the previous one."""
    instants = itertools.count(0, pas)
    return lambda: next(instants)


class StreamTest(unittest.TestCase):
    def test_the_answer_is_assembled_from_many_pieces(self):
        fiche, env, _ = demander(fakes.FakeGemini(piece_size=30))
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(sum(len(partie.elements) for partie in fiche.parties), 3)
        self.assertEqual(modeles(env), ["fake-model-a"])

    def test_a_message_shows_that_a_long_answer_is_still_arriving(self):
        with mock.patch.object(gemini_client.time, "monotonic", horloge(4)):
            _, _, affichage = demander(fakes.FakeGemini(piece_size=300))
        lignes = [ligne for ligne in affichage.splitlines() if "réponse en cours" in ligne]
        self.assertGreaterEqual(len(lignes), 2)
        tailles = [int(ligne.split(":")[1].split()[0]) for ligne in lignes]
        self.assertEqual(tailles, sorted(tailles))
        self.assertGreater(tailles[-1], tailles[0])

    def test_a_quick_answer_prints_no_progress_message(self):
        _, _, affichage = demander(fakes.FakeGemini())
        self.assertNotIn("réponse en cours", affichage)

    def test_the_interval_between_progress_messages_comes_from_the_settings(self):
        with mock.patch.object(gemini_client.time, "monotonic", horloge(1)), mock.patch.object(settings, "INTERVALLE_PROGRES", 100):
            _, _, affichage = demander(fakes.FakeGemini(piece_size=300))
        self.assertNotIn("réponse en cours", affichage)

    def test_a_long_answer_is_never_cut_while_it_keeps_coming(self):
        # 30 seconds between pieces and about 25 pieces: 12 minutes in all, far beyond the 60 seconds of silence allowed.
        with mock.patch.object(gemini_client.time, "monotonic", horloge(30)), mock.patch.object(settings, "DUREE_MAX_REPONSE", 100000):
            fiche, env, affichage = demander(fakes.FakeGemini(piece_size=100))
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a"])
        self.assertNotIn("n'a pas répondu", affichage)
        self.assertNotIn("écrit encore sa réponse", affichage)

    def test_an_answer_that_never_ends_is_stopped_after_the_maximum_duration(self):
        gemini = fakes.FakeGemini(endless=True, piece_size=200)
        with mock.patch.object(gemini_client.time, "monotonic", horloge(1)), mock.patch.object(settings, "DUREE_MAX_REPONSE", 20):
            erreur, env, affichage = demander(gemini)
        self.assertIsInstance(erreur, RuntimeError)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-b"])  # no second try of a model that loops
        self.assertEqual(env.pauses, [])
        self.assertIn("le modèle fake-model-a écrit encore sa réponse après 20 s", affichage)
        self.assertIn("écrit encore sa réponse après 20 s", str(erreur))

    def test_the_last_model_does_not_announce_a_next_one_when_it_loops(self):
        gemini = fakes.FakeGemini(endless=True, piece_size=200)
        with mock.patch.object(gemini_client.time, "monotonic", horloge(1)), mock.patch.object(settings, "DUREE_MAX_REPONSE", 20):
            _, _, affichage = demander(gemini)
        derniere = [ligne for ligne in affichage.splitlines() if "fake-model-b écrit encore" in ligne][0]
        self.assertNotIn("Essai du modèle suivant", derniere)

    def test_a_stream_that_goes_silent_in_the_middle_gives_way_to_the_next_model(self):
        gemini = fakes.FakeGemini(stall_after=1, piece_size=100)
        erreur, env, affichage = demander(gemini)
        self.assertIsInstance(erreur, RuntimeError)
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-b"])
        self.assertIn(f"le modèle fake-model-a n'a pas répondu en {settings.DELAI_APPEL_GEMINI} s", affichage)

    def test_the_stream_is_closed_when_it_is_given_up(self):
        class Suivi(fakes.FakeGemini):
            def generate_content_stream(self, model, contents, config):
                self.dernier_flux = super().generate_content_stream(model, contents, config)
                return self.dernier_flux

        gemini = Suivi(endless=True, piece_size=200)
        with mock.patch.object(gemini_client.time, "monotonic", horloge(1)), mock.patch.object(settings, "DUREE_MAX_REPONSE", 20):
            demander(gemini)
        self.assertIsNone(gemini.dernier_flux.gi_frame)  # a generator that was closed has no frame left

    def test_a_truncated_answer_is_a_failed_try(self):
        class Coupe(fakes.FakeGemini):
            def generate_content(self, model, contents, config):
                reponse = super().generate_content(model, contents, config)
                reponse.text = reponse.text[:200]
                return reponse

        erreur, env, _ = demander(Coupe())
        self.assertIsInstance(erreur, RuntimeError)
        self.assertEqual(len(env.gemini.calls), 4)  # two tries on each model

    def test_the_default_settings_make_sense(self):
        self.assertGreater(settings.DUREE_MAX_REPONSE, settings.DELAI_APPEL_GEMINI)
        self.assertGreater(settings.INTERVALLE_PROGRES, 0)


class ThoughtSummaryTest(unittest.TestCase):
    """The summary of the thoughts keeps the silence away while a model thinks, and shows that it works."""

    def test_the_summary_is_asked_for_by_default(self):
        _, env, _ = demander(fakes.FakeGemini())
        self.assertTrue(env.gemini.calls[0]["thinking"].include_thoughts)

    def test_it_is_not_asked_for_when_the_setting_is_off(self):
        with mock.patch.object(settings, "RESUME_PENSEES", False):
            _, env, _ = demander(fakes.FakeGemini())
        self.assertIsNone(env.gemini.calls[0]["thinking"])

    def test_the_thoughts_are_not_part_of_the_answer(self):
        fiche, env, _ = demander(fakes.FakeGemini(thinking_pieces=3))
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a"])

    def test_a_message_shows_that_the_model_is_thinking(self):
        # 4 seconds between pieces and an interval of 10: the message comes with the third piece of thoughts
        with mock.patch.object(gemini_client.time, "monotonic", horloge(4)):
            _, _, affichage = demander(fakes.FakeGemini(thinking_pieces=3, piece_size=100000))
        lignes = [ligne for ligne in affichage.splitlines() if "le modèle réfléchit" in ligne]
        self.assertEqual(len(lignes), 1)
        self.assertIn("caractères de pensées reçus", lignes[0])
        self.assertNotIn("réponse en cours", affichage.split("le modèle réfléchit")[0])

    def test_once_the_answer_starts_the_message_is_about_the_answer(self):
        with mock.patch.object(gemini_client.time, "monotonic", horloge(4)):
            _, _, affichage = demander(fakes.FakeGemini(thinking_pieces=1, piece_size=200))
        self.assertIn("réponse en cours", affichage)

    def test_a_model_that_refuses_the_option_is_asked_again_at_once_without_it(self):
        fiche, env, affichage = demander(fakes.FakeGemini(thinking_error_models={"fake-model-a"}))
        self.assertIsInstance(fiche, FicheCours)
        calls = env.gemini.calls
        self.assertEqual([c["model"] for c in calls], ["fake-model-a", "fake-model-a"])
        self.assertIsNotNone(calls[0]["thinking"])
        self.assertIsNone(calls[1]["thinking"])
        self.assertEqual(env.pauses, [])  # it is not a failed try
        self.assertIn("le modèle fake-model-a n'accepte pas les réglages de réflexion (RESUME_PENSEES, NIVEAU_REFLEXION, BUDGET_REFLEXION). On continue sans.", affichage)

    def test_the_refusal_is_remembered_for_the_rest_of_the_run(self):
        with fakes.environment(gemini=fakes.FakeGemini(thinking_error_models={"fake-model-a"})) as env:
            gemini_client.generer_fiche("prompt", FicheCours)
            gemini_client.generer_fiche("prompt", FicheCours)
        self.assertEqual([c["thinking"] is not None for c in env.gemini.calls], [True, False, False])

    def test_another_error_400_is_not_taken_for_a_refusal(self):
        class Invalide(fakes.FakeGemini):
            def generate_content(self, model, contents, config):
                super().generate_content(model, contents, config)
                raise errors.ClientError(400, {"error": {"code": 400, "message": "Request contains an invalid argument.", "status": "INVALID_ARGUMENT"}})

        with fakes.environment(gemini=Invalide()) as env:
            sortie = io.StringIO()
            with contextlib.redirect_stdout(sortie), self.assertRaises(RuntimeError):
                gemini_client.generer_fiche("prompt", FicheCours)
            self.assertEqual(gemini_client._modeles_sans_pensees, set())
        self.assertEqual(len(env.gemini.calls), 4)  # two tries on each model, no hidden retry

    def test_how_a_refusal_is_recognised(self):
        refus = errors.ClientError(400, {"error": {"message": "Thinking is not supported by this model."}})
        self.assertTrue(gemini_client._est_une_erreur_de_reflexion(refus))
        self.assertFalse(gemini_client._est_une_erreur_de_reflexion(errors.ClientError(400, {"error": {"message": "invalid argument"}})))
        self.assertFalse(gemini_client._est_une_erreur_de_reflexion(errors.ServerError(503, {"error": {"message": "thinking is overloaded"}})))
        self.assertFalse(gemini_client._est_une_erreur_de_reflexion(RuntimeError("thinking")))

    def test_the_size_of_the_thoughts_is_read_from_the_pieces(self):
        self.assertEqual(gemini_client._taille_pensees(fakes.FakeMorceau(pensees="12345")), 5)
        self.assertEqual(gemini_client._taille_pensees(fakes.FakeMorceau(text="the answer")), 0)
        self.assertEqual(gemini_client._taille_pensees(fakes.FakeMorceau()), 0)

    def test_the_default_limits_leave_room_for_a_long_thinking(self):
        self.assertGreaterEqual(settings.DELAI_APPEL_GEMINI, 300)
        self.assertGreater(settings.DUREE_MAX_REPONSE, settings.DELAI_APPEL_GEMINI)


class ThinkingSettingsTest(unittest.TestCase):
    """NIVEAU_REFLEXION and BUDGET_REFLEXION tell a model how much to think."""

    def demander_avec(self, **reglages):
        with mock.patch.multiple(settings, **reglages):
            return demander(fakes.FakeGemini())

    def test_by_default_only_the_summary_is_asked_for(self):
        _, env, _ = demander(fakes.FakeGemini())
        reflexion = env.gemini.calls[0]["thinking"]
        self.assertTrue(reflexion.include_thoughts)
        self.assertIsNone(reflexion.thinking_level)
        self.assertIsNone(reflexion.thinking_budget)

    def test_a_level_is_passed_to_the_model(self):
        _, env, _ = self.demander_avec(NIVEAU_REFLEXION="high")
        self.assertEqual(env.gemini.calls[0]["thinking"].thinking_level.name, "HIGH")

    def test_a_budget_is_passed_to_the_model(self):
        _, env, _ = self.demander_avec(BUDGET_REFLEXION=8192)
        self.assertEqual(env.gemini.calls[0]["thinking"].thinking_budget, 8192)

    def test_a_budget_of_zero_is_passed_too(self):
        # 0 means "no thinking": it must not be mistaken for "not set".
        _, env, _ = self.demander_avec(BUDGET_REFLEXION=0)
        self.assertEqual(env.gemini.calls[0]["thinking"].thinking_budget, 0)

    def test_a_dynamic_budget_is_passed(self):
        _, env, _ = self.demander_avec(BUDGET_REFLEXION=-1)
        self.assertEqual(env.gemini.calls[0]["thinking"].thinking_budget, -1)

    def test_the_level_works_without_the_summary(self):
        _, env, _ = self.demander_avec(RESUME_PENSEES=False, NIVEAU_REFLEXION="medium")
        reflexion = env.gemini.calls[0]["thinking"]
        self.assertFalse(reflexion.include_thoughts)
        self.assertEqual(reflexion.thinking_level.name, "MEDIUM")

    def test_nothing_is_asked_when_every_option_is_off(self):
        _, env, _ = self.demander_avec(RESUME_PENSEES=False, NIVEAU_REFLEXION=None, BUDGET_REFLEXION=None)
        self.assertIsNone(env.gemini.calls[0]["thinking"])

    def test_a_model_that_refuses_the_settings_is_asked_again_without_any(self):
        with mock.patch.multiple(settings, NIVEAU_REFLEXION="high"):
            fiche, env, affichage = demander(fakes.FakeGemini(thinking_error_models={"fake-model-a"}))
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual([c["thinking"] is not None for c in env.gemini.calls], [True, False])
        self.assertIn("n'accepte pas les réglages de réflexion", affichage)

    def test_the_config_helper_returns_none_for_a_model_that_refused(self):
        with fakes.environment():
            gemini_client._modeles_sans_pensees.add("fake-model-a")
            self.assertIsNone(gemini_client.config_reflexion("fake-model-a"))
            self.assertIsNotNone(gemini_client.config_reflexion("fake-model-b"))


class WaitingMessageTest(unittest.TestCase):
    """While Gemini is silent, a message says so, so that a wait does not look like a freeze."""

    def test_a_message_is_printed_while_gemini_is_silent(self):
        with mock.patch.object(settings, "INTERVALLE_PROGRES", 0.05):
            fiche, _, affichage = demander(fakes.FakeGemini(first_piece_delay=0.4))
        self.assertIsInstance(fiche, FicheCours)
        attentes = [ligne for ligne in affichage.splitlines() if "en attente de Gemini" in ligne]
        self.assertGreaterEqual(len(attentes), 2)
        self.assertIn(f"(abandon à {settings.DELAI_APPEL_GEMINI} s)", attentes[0])

    def test_a_quick_answer_prints_no_waiting_message(self):
        with mock.patch.object(settings, "INTERVALLE_PROGRES", 0.5):
            _, _, affichage = demander(fakes.FakeGemini())
        self.assertNotIn("en attente de Gemini", affichage)

    def test_the_helper_thread_is_gone_when_the_answer_is_read(self):
        avant = threading.active_count()
        with mock.patch.object(settings, "INTERVALLE_PROGRES", 0.05):
            demander(fakes.FakeGemini(first_piece_delay=0.2))
        self.assertEqual(threading.active_count(), avant)

    def test_the_helper_thread_is_gone_after_a_failure_too(self):
        avant = threading.active_count()
        with mock.patch.object(settings, "INTERVALLE_PROGRES", 0.05):
            demander(fakes.FakeGemini(failing_models={"fake-model-a", "fake-model-b"}, first_piece_delay=0.1))
        self.assertEqual(threading.active_count(), avant)

    def test_the_default_silence_limit_leaves_time_to_read_a_long_pdf(self):
        # With 60 seconds, the first model of a 158-slide course was given up before it wrote anything.
        self.assertGreaterEqual(settings.DELAI_APPEL_GEMINI, 120)


class UploadedFileTest(unittest.TestCase):
    def televerser(self, gemini, chemin="/folder/cours.pdf"):
        with fakes.environment(gemini=gemini) as env:
            with mock.patch.object(settings, "INTERVALLE_SONDAGE_FICHIER", 2), mock.patch.object(settings, "DELAI_TRAITEMENT_FICHIER", 6):
                try:
                    resultat = gemini_client.televerser_fichier(chemin)
                except Exception as erreur:
                    resultat = erreur
        return resultat, env

    def test_a_file_that_is_ready_at_once_is_returned_without_any_wait(self):
        fichier, env = self.televerser(fakes.FakeGemini())
        self.assertEqual(fichier.name, "files/cours.pdf")
        self.assertEqual(env.pauses, [])
        self.assertEqual(env.gemini.polls, 0)

    def test_a_file_that_is_still_being_read_is_waited_for(self):
        fichier, env = self.televerser(fakes.FakeGemini(processing_polls=2))
        self.assertEqual(fichier.state.name, "ACTIVE")
        self.assertEqual(env.gemini.polls, 3)  # two checks find it still being read, the third finds it ready
        self.assertEqual(env.pauses, [2, 2, 2])  # one wait before each check

    def test_a_file_that_never_gets_ready_gives_a_clear_error_and_does_not_wait_forever(self):
        erreur, env = self.televerser(fakes.FakeGemini(processing_polls=1000))
        self.assertIsInstance(erreur, TimeoutError)
        self.assertIn("n'a pas fini de lire le fichier 'files/cours.pdf' en 6 s", str(erreur))
        self.assertEqual(len(env.pauses), 3)

    def test_a_file_that_gemini_cannot_read_gives_a_clear_error(self):
        erreur, _ = self.televerser(fakes.FakeGemini(failed_files=True))
        self.assertIsInstance(erreur, RuntimeError)
        self.assertIn("n'a pas pu lire le fichier", str(erreur))

    def test_files_are_deleted_when_the_work_inside_fails(self):
        with fakes.environment() as env:
            with self.assertRaises(ValueError):
                with gemini_client.fichiers_televerses(["/a/un.pdf", "/a/deux.pdf"]):
                    raise ValueError("generation failed")
        self.assertEqual(env.gemini.deleted, ["files/un.pdf", "files/deux.pdf"])

    def test_the_first_file_is_deleted_when_the_second_upload_fails(self):
        class Panne(fakes.FakeGemini):
            def upload(self, file):
                if file.endswith("deux.pdf"):
                    raise RuntimeError("upload failed (fake)")
                return super().upload(file)

        with fakes.environment(gemini=Panne()) as env:
            with self.assertRaises(RuntimeError):
                with gemini_client.fichiers_televerses(["/a/un.pdf", "/a/deux.pdf"]):
                    pass
        self.assertEqual(env.gemini.deleted, ["files/un.pdf"])

    def test_nothing_is_uploaded_when_there_is_nothing_to_send(self):
        with fakes.environment() as env:
            with gemini_client.fichiers_televerses([]) as fichiers:
                self.assertEqual(fichiers, [])
        self.assertEqual(env.gemini.uploads, [])

    def test_a_failing_delete_is_reported_but_never_raised(self):
        with fakes.environment(gemini=fakes.FakeGemini(delete_fails=True)):
            sortie = io.StringIO()
            with contextlib.redirect_stdout(sortie):
                gemini_client.supprimer_fichier(fakes.FakeFichier("x", "files/x"))
        self.assertIn("n'a pas pu être supprimé", sortie.getvalue())

    def test_a_pdf_is_uploaded_and_a_notebook_is_read_as_text(self):
        with fakes.environment() as env:
            with mock.patch.object(gemini_client, "extraire_contenu_fichier", lambda chemin: f"texte de {chemin}"):
                with gemini_client.documents_pour_gemini([("Cours", "/a/cours.pdf"), ("Lab", "/a/lab.ipynb")]) as (parties, textes):
                    pass
        self.assertEqual(parties, ["Cours (PDF joint) :", "<uploaded:cours.pdf>"])
        self.assertEqual(textes, {"Cours": None, "Lab": "texte de /a/lab.ipynb"})
        self.assertEqual(env.gemini.uploads, ["cours.pdf"])

    def test_with_the_setting_off_a_pdf_is_read_as_text(self):
        with fakes.environment() as env:
            with mock.patch.object(settings, "PDF_VIA_GEMINI", False), \
                    mock.patch.object(gemini_client, "extraire_contenu_fichier", lambda chemin: "texte"):
                with gemini_client.documents_pour_gemini([("Cours", "/a/cours.PDF")]) as (parties, textes):
                    pass
        self.assertEqual((parties, textes, env.gemini.uploads), ([], {"Cours": "texte"}, []))

    def test_the_extension_of_a_pdf_is_recognised_in_capitals(self):
        with fakes.environment() as env:
            with gemini_client.documents_pour_gemini([("Cours", "/a/COURS.PDF")]) as (parties, textes):
                pass
        self.assertEqual(env.gemini.uploads, ["COURS.PDF"])


class EmptySheetTest(unittest.TestCase):
    def test_an_empty_sheet_counts_as_a_failed_try_and_the_next_one_is_used(self):
        gemini = fakes.FakeGemini(answers=lambda modele, schema, contenu: vide() if modele == "fake-model-a" else fakes.cours_answer())
        fiche, env, affichage = demander(gemini)
        self.assertFalse(fiche.est_vide())
        self.assertEqual(modeles(env), ["fake-model-a", "fake-model-a", "fake-model-b"])
        self.assertIn("fiche vide", affichage)

    def test_when_every_answer_is_empty_an_error_is_raised(self):
        erreur, env, _ = demander(fakes.FakeGemini(answers=lambda modele, schema, contenu: vide()))
        self.assertIsInstance(erreur, RuntimeError)
        self.assertIn("fiche vide", str(erreur))
        self.assertEqual(len(env.gemini.calls), 4)

    def test_every_type_of_sheet_knows_when_it_is_empty(self):
        self.assertTrue(FicheCours.model_validate(vide()).est_vide())
        parties_sans_element = [{**partie, "elements": []} for partie in fakes.cours_answer()["parties"]]
        self.assertTrue(FicheCours.model_validate({**fakes.cours_answer(), "parties": parties_sans_element}).est_vide())
        self.assertTrue(FicheExercices.model_validate({**fakes.exercices_answer(), "exercices": []}).est_vide())
        self.assertTrue(FicheLab.model_validate({**fakes.lab_answer(), "parties": []}).est_vide())
        self.assertTrue(FicheNotesManuscrites.model_validate({**fakes.notes_answer(), "sections": []}).est_vide())
        self.assertFalse(FicheLab.model_validate(fakes.lab_answer()).est_vide())


class FormulasOutsideDollarsTest(unittest.TestCase):
    def test_a_sheet_with_raw_latex_is_kept_and_no_other_model_is_asked(self):
        # The raw LaTeX is repaired when the page is built, so a second model would only cost a call.
        gemini = fakes.FakeGemini(answers=lambda modele, schema, contenu: sans_dollars())
        fiche, env, _ = demander(gemini)
        self.assertIsInstance(fiche, FicheCours)
        self.assertEqual(modeles(env), ["fake-model-a"])
        self.assertEqual(env.pauses, [])

    def test_the_warning_says_how_many_extracts_were_found_and_that_they_are_repaired(self):
        gemini = fakes.FakeGemini(answers=lambda modele, schema, contenu: sans_dollars())
        _, _, affichage = demander(gemini)
        self.assertIn("le modèle fake-model-a a écrit", affichage)
        self.assertIn("extrait(s) de LaTeX hors de $...$", affichage)
        self.assertIn("Ils sont mis entre $ automatiquement", affichage)

    def test_a_clean_answer_gives_no_warning(self):
        _, env, affichage = demander(fakes.FakeGemini())
        self.assertEqual(modeles(env), ["fake-model-a"])
        self.assertNotIn("LaTeX hors de", affichage)

    def test_the_code_of_a_lab_is_never_taken_for_raw_latex(self):
        code = "x <- c(1, 2)  # uses \\frac and h_{ij} in a comment, but it is code"
        reponse = fakes.lab_answer()
        reponse["parties"][0]["code_solution_propre"] = code
        fiche = FicheLab.model_validate(reponse)
        self.assertEqual(gemini_client.formules_hors_dollars(fiche), [])

    def test_the_extracts_are_counted_before_the_repair(self):
        fiche = FicheCours.model_validate(sans_dollars())
        self.assertTrue(gemini_client.formules_hors_dollars(fiche))


class FormulaInstructionTest(unittest.TestCase):
    def test_the_instruction_names_the_commands_with_a_single_backslash(self):
        self.assertIn("\\leq, \\frac", CONSIGNE_FORMULES)
        self.assertNotIn("\\\\leq", CONSIGNE_FORMULES)

    def test_every_prompt_that_asks_for_formulas_carries_it(self):
        demandes = [prompts.prompt_cours("x"), prompts.prompt_exercices("x", "y"), prompts.prompt_lab("x", "y"),
                    prompts.prompt_session(None, ""), prompts.prompt_linter("{}")]
        for prompt in demandes:
            self.assertIn(CONSIGNE_FORMULES, prompt)

    def test_the_fields_with_formulas_carry_it_in_the_schema(self):
        champs = [FicheCours.model_json_schema()["$defs"]["ElementMathematique"]["properties"]["enonce_latex"],
                  FicheExercices.model_json_schema()["$defs"]["ExerciceCorrige"]["properties"]["correction_etofee_latex"],
                  FicheNotesManuscrites.model_json_schema()["$defs"]["SectionNote"]["properties"]["contenu_latex"]]
        for champ in champs:
            self.assertIn(CONSIGNE_FORMULES, champ["description"])


if __name__ == "__main__":
    unittest.main()
