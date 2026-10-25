"""Tests of the Notion blocks and of the calls to the Notion API (fake Notion)."""
import json
import unittest

import notion_api
import prompts
import settings
from notion_blocks import (
    blocs_code,
    blocs_depuis_rich_text,
    construire_page,
    convertir_equations_en_texte,
    creer_toggles,
    nettoyer_titre,
    texte_vers_rich_text_latex,
)
from schemas import (
    CONSIGNE_TITRE,
    ExerciceCorrige,
    FicheCours,
    FicheExercices,
    FicheLab,
    FicheNotesManuscrites,
)
from tests import fakes


class BlockTest(unittest.TestCase):
    def test_a_long_toggle_is_split_instead_of_cut(self):
        toggles = creer_toggles("Démonstration", [{"object": "block"}] * 200)
        self.assertEqual([len(t["toggle"]["children"]) for t in toggles], [90, 90, 20])
        self.assertEqual(toggles[1]["toggle"]["rich_text"][0]["text"]["content"], "Démonstration (suite 1)")

    def test_a_rich_text_of_more_than_100_items_is_spread_over_blocks(self):
        rich_text = texte_vers_rich_text_latex(" ".join(f"$x_{i}$ t" for i in range(150)))
        self.assertGreater(len(rich_text), 100)
        blocs = blocs_depuis_rich_text(rich_text)
        self.assertEqual(sum(len(b["paragraph"]["rich_text"]) for b in blocs), len(rich_text))
        self.assertTrue(all(len(b["paragraph"]["rich_text"]) <= 100 for b in blocs))

    def test_a_very_long_code_gives_several_blocks_and_loses_nothing(self):
        blocs = blocs_code("x" * 400000, "python")
        self.assertEqual(len(blocs), 3)
        total = sum(len(item["text"]["content"]) for b in blocs for item in b["code"]["rich_text"])
        self.assertEqual(total, 400000)

    def test_an_empty_code_still_gives_one_block(self):
        self.assertEqual(len(blocs_code("", "python")), 1)

    def test_the_fallback_turns_equations_into_text_at_any_depth(self):
        blocs = [{"object": "block", "type": "toggle", "toggle": {"rich_text": [], "children": [
            {"object": "block", "type": "equation", "equation": {"expression": "x^2"}},
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
                {"type": "equation", "equation": {"expression": "y"}},
                {"type": "text", "text": {"content": "t"}}]}},
        ]}}]
        convertir_equations_en_texte(blocs)
        texte = json.dumps(blocs)
        self.assertNotIn('"equation"', texte)
        self.assertIn("$$ x^2 $$", texte)
        self.assertIn("$y$", texte)

    def test_the_course_page_has_title_category_toggle_and_short_equations(self):
        titre, categorie, blocs = construire_page(FicheCours.model_validate(fakes.cours_answer()))
        self.assertEqual((titre, categorie), ("Chapter 1: Ridge regression", "Cours fiché"))
        toggles = [b for b in blocs if b["type"] == "toggle"]
        self.assertEqual(len(toggles), 1)
        equations = [b for t in toggles for b in t["toggle"]["children"] if b["type"] == "equation"]
        self.assertTrue(equations)
        self.assertTrue(all(len(b["equation"]["expression"]) <= 1000 for b in equations))

    def test_a_lab_page_uses_the_forced_category_and_a_lowercase_language(self):
        fiche = FicheLab.model_validate(fakes.lab_answer())
        self.assertEqual(fiche.langage_principal, "r")
        titre, categorie, blocs = construire_page(fiche, categorie_forcee="Lab")
        self.assertEqual(categorie, "Lab")
        self.assertEqual({b["code"]["language"] for b in blocs if b["type"] == "code"}, {"r", "python"})


class TitleTest(unittest.TestCase):
    def test_a_short_title_is_left_alone(self):
        self.assertEqual(nettoyer_titre("Régression Ridge"), "Régression Ridge")

    def test_spaces_and_line_breaks_are_collapsed(self):
        self.assertEqual(nettoyer_titre("  Régression\n  Ridge  "), "Régression Ridge")

    def test_dollar_signs_are_removed(self):
        self.assertNotIn("$", nettoyer_titre("Estimation de $\\beta$ par moindres carrés"))

    def test_an_empty_title_gets_a_default(self):
        self.assertEqual(nettoyer_titre("$$"), "Sans titre")
        self.assertEqual(nettoyer_titre(""), "Sans titre")

    def test_a_long_title_is_cut_at_a_word_and_never_exceeds_the_limit(self):
        titre = nettoyer_titre("Estimation par moindres carrés pénalisés et sélection de modèle par validation croisée", limite=40)
        self.assertLessEqual(len(titre), 40)
        self.assertTrue(titre.endswith("…"))
        self.assertEqual(titre, "Estimation par moindres carrés…")

    def test_a_long_title_without_spaces_is_cut_at_the_limit(self):
        titre = nettoyer_titre("x" * 200, limite=30)
        self.assertEqual(len(titre), 30)

    def test_the_default_limit_comes_from_the_settings(self):
        self.assertLessEqual(len(nettoyer_titre("mot " * 100)), settings.LIMITE_TITRE)

    def test_the_four_types_of_page_use_the_same_cleaning(self):
        longs = {"titre_cours": "A " * 100, "titre_sujet": "B " * 100, "titre_lab": "C " * 100, "titre_note": "D " * 100}
        fiches = [
            FicheCours.model_validate({**fakes.cours_answer(), "titre_cours": longs["titre_cours"]}),
            FicheExercices.model_validate({**fakes.exercices_answer(), "titre_sujet": longs["titre_sujet"]}),
            FicheLab.model_validate({**fakes.lab_answer(), "titre_lab": longs["titre_lab"]}),
            FicheNotesManuscrites.model_validate({**fakes.notes_answer(), "titre_note": longs["titre_note"]}),
        ]
        for fiche in fiches:
            with self.subTest(type=fiche.type_fiche):
                titre = construire_page(fiche)[0]
                self.assertLessEqual(len(titre), settings.LIMITE_TITRE)
                self.assertTrue(titre.endswith("…"))

    def test_the_four_title_fields_share_one_instruction_that_states_the_limit(self):
        champs = [FicheCours.model_fields["titre_cours"], FicheExercices.model_fields["titre_sujet"],
                  FicheLab.model_fields["titre_lab"], FicheNotesManuscrites.model_fields["titre_note"]]
        for champ in champs:
            self.assertEqual(champ.description, CONSIGNE_TITRE)
        self.assertIn(str(settings.LIMITE_TITRE), CONSIGNE_TITRE)

    def test_every_prompt_that_asks_for_a_title_carries_the_same_instruction(self):
        demandes = [prompts.prompt_cours("x"), prompts.prompt_exercices("x", "y"), prompts.prompt_lab("x", "y"),
                    prompts.prompt_session(None, "")]
        for prompt in demandes:
            self.assertIn(CONSIGNE_TITRE, prompt)


class ApiTest(unittest.TestCase):
    def test_the_page_is_created_with_its_properties(self):
        with fakes.environment() as env:
            fiche = FicheCours.model_validate(fakes.cours_answer())
            notion_api.envoyer_vers_notion(fiche, settings.NOTION_DATABASE_ID)
        corps = env.notion.page_bodies[0]
        self.assertEqual(corps["properties"]["Cours"], {"select": {"name": "Machine Learning"}})
        self.assertEqual(corps["properties"]["Catégorie"], {"select": {"name": "Cours fiché"}})
        self.assertEqual(corps["properties"]["Name"]["title"][0]["text"]["content"], "Chapter 1: Ridge regression")
        self.assertEqual(corps["parent"], {"database_id": fakes.FAKE_DATABASE_ID})

    def test_an_empty_sheet_is_refused_before_anything_is_sent(self):
        # Regression: a PDF that gave no content created an empty page and was archived as a success.
        fiche = FicheCours.model_validate({**fakes.cours_answer(), "parties": []})
        with fakes.environment() as env:
            with self.assertRaises(RuntimeError) as contexte:
                notion_api.envoyer_vers_notion(fiche, settings.NOTION_DATABASE_ID)
        self.assertIn("Fiche vide", str(contexte.exception))
        self.assertEqual(env.notion.requests, [])

    def test_the_token_is_sent_to_notion(self):
        with fakes.environment() as env:
            notion_api.envoyer_vers_notion(FicheCours.model_validate(fakes.cours_answer()), settings.NOTION_DATABASE_ID)
        self.assertTrue(all(r["headers"]["Authorization"] == "Bearer <TOKEN>" for r in env.notion.requests))

    def test_after_a_first_refusal_the_properties_are_kept_and_equations_become_text(self):
        # Regression: the first version of the fallback dropped Cours and Catégorie, so the page
        # could no longer be found by recuperer_dernier_cours_notion.
        with fakes.environment(notion=fakes.FakeNotion(reject_first_post=True)) as env:
            notion_api.envoyer_vers_notion(FicheCours.model_validate(fakes.cours_answer()), settings.NOTION_DATABASE_ID)
        self.assertEqual(len(env.notion.page_bodies), 2)
        second = env.notion.page_bodies[1]
        self.assertIn("Cours", second["properties"])
        self.assertIn("Catégorie", second["properties"])
        self.assertNotIn('"type": "equation"', json.dumps(second["children"]))

    def test_when_notion_refuses_every_time_an_error_is_raised(self):
        with fakes.environment(notion=fakes.FakeNotion(reject_all_posts=True)) as env:
            with self.assertRaises(RuntimeError):
                notion_api.envoyer_vers_notion(FicheCours.model_validate(fakes.cours_answer()), settings.NOTION_DATABASE_ID)
        self.assertEqual(len(env.notion.page_bodies), 3)
        self.assertEqual(list(env.notion.page_bodies[2]["properties"]), ["Name"])

    def test_blocks_past_the_first_hundred_are_appended(self):
        exercices = [ExerciceCorrige(numero_exo=f"Exercise {i}", enonce_latex="Statement.", correction_etofee_latex="Answer.")
                     for i in range(60)]
        fiche = FicheExercices(titre_sujet="Big sheet", nom_du_cours_notion="Machine Learning",
                               exercices=exercices, methode_a_retenir=["Method."])
        total = len(construire_page(fiche)[2])
        self.assertGreater(total, 100)
        with fakes.environment() as env:
            notion_api.envoyer_vers_notion(fiche, settings.NOTION_DATABASE_ID)
        envoyes = len(env.notion.page_bodies[0]["children"]) + sum(len(p["children"]) for p in env.notion.patch_bodies)
        self.assertEqual(envoyes, total)

    def test_a_refused_append_is_retried_with_equations_as_text(self):
        bloc = {"object": "block", "type": "equation", "equation": {"expression": "x^2"}}
        with fakes.environment(notion=fakes.FakeNotion(reject_first_patch=True)) as env:
            notion_api.ajouter_blocs("page-1", [bloc])
        self.assertEqual(len(env.notion.patch_bodies), 2)
        self.assertEqual(env.notion.patch_bodies[1]["children"][0]["type"], "paragraph")

    def test_the_latest_course_is_read_with_its_text(self):
        with fakes.environment():
            cours = notion_api.recuperer_dernier_cours_notion("Machine Learning")
            absent = notion_api.recuperer_dernier_cours_notion("Probability Refresher")
        self.assertEqual(cours["titre"], "Chapter 1: Ridge regression")
        self.assertIn("Definition 1. Ridge estimator.", cours["contenu"])
        self.assertIsNone(absent)


if __name__ == "__main__":
    unittest.main()
