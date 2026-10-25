"""Tests of the course sheet: its parts, its explanations, its layout in Notion and its prompt."""
import unittest
from typing import get_args

from pydantic import ValidationError

import gemini_client
import prompts
from notion_blocks import blocs_avec_etiquette, creer_blocs_avec_maths, construire_page
from schemas import (
    CONSIGNE_EXPLICATION,
    CONSIGNE_FIL_CONDUCTEUR,
    CONSIGNE_FORMULES,
    CONSIGNE_INTRODUCTION_PARTIE,
    ElementMathematique,
    FicheCours,
    PartieCours,
)
from tests import fakes


def fiche(**changes):
    return FicheCours.model_validate({**fakes.cours_answer(), **changes})


def etiquette(bloc):
    """The bold label at the start of a paragraph, or None."""
    if bloc["type"] != "paragraph":
        return None
    premier = bloc["paragraph"]["rich_text"][0]
    return premier["text"]["content"] if premier.get("annotations", {}).get("bold") else None


def texte_du_bloc(bloc):
    return "".join(item["text"]["content"] for item in bloc["paragraph"]["rich_text"] if item["type"] == "text")


def titres(blocs, niveau):
    return [b[niveau]["rich_text"][0]["text"]["content"] for b in blocs if b["type"] == niveau]


class SchemaTest(unittest.TestCase):
    def test_the_new_types_of_element_are_allowed(self):
        types = get_args(ElementMathematique.model_fields["type_element"].annotation)
        for attendu in ("Définition", "Théorème", "Concept clé", "Formule", "Algorithme", "Exemple"):
            self.assertIn(attendu, types)

    def test_an_element_needs_its_explanation(self):
        element = dict(fakes.cours_answer()["parties"][0]["elements"][0])
        sans = {k: v for k, v in element.items() if k != "explication"}
        with self.assertRaises(ValidationError):
            ElementMathematique.model_validate(sans)

    def test_a_part_needs_its_introduction(self):
        partie = dict(fakes.cours_answer()["parties"][0])
        with self.assertRaises(ValidationError):
            PartieCours.model_validate({k: v for k, v in partie.items() if k != "introduction"})

    def test_a_course_needs_its_overview_and_its_parts(self):
        for champ in ("fil_conducteur", "parties"):
            with self.subTest(field=champ):
                with self.assertRaises(ValidationError):
                    FicheCours.model_validate({k: v for k, v in fakes.cours_answer().items() if k != champ})

    def test_a_key_concept_may_have_no_formal_statement(self):
        concept = fiche().parties[1].elements[0]
        self.assertEqual((concept.type_element, concept.enonce_latex), ("Concept clé", ""))

    def test_the_old_labels_of_each_element_are_gone(self):
        self.assertNotIn("pourquoi_dans_le_cours", ElementMathematique.model_fields)
        self.assertNotIn("explication_simple", ElementMathematique.model_fields)

    def test_the_instructions_are_carried_by_the_schema(self):
        self.assertEqual(ElementMathematique.model_fields["explication"].description, CONSIGNE_EXPLICATION)
        self.assertEqual(PartieCours.model_fields["introduction"].description, CONSIGNE_INTRODUCTION_PARTIE)
        self.assertEqual(FicheCours.model_fields["fil_conducteur"].description, CONSIGNE_FIL_CONDUCTEUR)
        self.assertIn("EN ENTIER", ElementMathematique.model_fields["enonce_latex"].description)

    def test_the_texts_to_check_for_formulas_include_the_new_fields(self):
        textes = fiche().textes_avec_formules()
        feuille = fakes.cours_answer()
        self.assertIn(feuille["fil_conducteur"], textes)
        self.assertIn(feuille["parties"][0]["introduction"], textes)
        self.assertIn(feuille["parties"][0]["elements"][0]["explication"], textes)

    def test_a_course_with_parts_but_no_element_is_empty(self):
        parties = [{**partie, "elements": []} for partie in fakes.cours_answer()["parties"]]
        self.assertTrue(fiche(parties=parties).est_vide())
        self.assertFalse(fiche().est_vide())


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.blocs = construire_page(fiche())[2]

    def test_the_page_starts_with_the_overview(self):
        self.assertEqual(self.blocs[0]["type"], "heading_2")
        self.assertEqual(self.blocs[0]["heading_2"]["rich_text"][0]["text"]["content"], "Vue d'ensemble")
        self.assertIn("overfit", texte_du_bloc(self.blocs[1]))
        self.assertEqual(self.blocs[2]["type"], "divider")

    def test_the_course_is_cut_into_numbered_parts(self):
        self.assertEqual(titres(self.blocs, "heading_2"), [
            "Vue d'ensemble", "Partie 1 : Penalising least squares", "Partie 2 : Choosing the penalty"])

    def test_each_part_starts_with_its_introduction_before_any_element(self):
        for debut in (i for i, b in enumerate(self.blocs) if b["type"] == "heading_2" and "Partie" in b["heading_2"]["rich_text"][0]["text"]["content"]):
            self.assertEqual(etiquette(self.blocs[debut + 1]), "Dans cette partie : ")
            self.assertEqual(self.blocs[debut + 2]["type"], "heading_3")

    def test_the_introduction_says_what_the_part_does(self):
        introduction = [b for b in self.blocs if etiquette(b) == "Dans cette partie : "][0]
        self.assertIn("overfits", texte_du_bloc(introduction))

    def test_the_formal_statement_comes_first_then_its_explanation(self):
        i = next(i for i, b in enumerate(self.blocs) if b["type"] == "heading_3")
        self.assertEqual(self.blocs[i]["heading_3"]["rich_text"][0]["text"]["content"], "Définition — Ridge estimator")
        j = next(k for k in range(i + 1, len(self.blocs)) if etiquette(self.blocs[k]) == "Explication : ")
        self.assertGreater(j, i + 1)  # the statement is there, and it has no label
        self.assertTrue(all(etiquette(b) is None for b in self.blocs[i + 1:j]))
        self.assertIn("brake", texte_du_bloc(self.blocs[j]))

    def test_there_is_a_single_label_per_element(self):
        etiquettes = [etiquette(b) for b in self.blocs if etiquette(b)]
        self.assertEqual(set(etiquettes), {"Dans cette partie : ", "Explication : "})
        self.assertNotIn("En clair : ", etiquettes)
        self.assertNotIn("Formellement : ", etiquettes)

    def test_a_concept_without_statement_goes_straight_to_its_explanation(self):
        titre = [i for i, b in enumerate(self.blocs) if b["type"] == "heading_3"][-1]
        concept = self.blocs[titre:]
        self.assertEqual([b["type"] for b in concept], ["heading_3", "paragraph", "divider"])
        self.assertEqual(etiquette(concept[1]), "Explication : ")

    def test_the_demonstration_stays_in_a_toggle_after_the_explanation(self):
        i = next(i for i, b in enumerate(self.blocs) if b["type"] == "toggle")
        self.assertEqual(etiquette(self.blocs[i - 1]), "Explication : ")
        self.assertEqual(self.blocs[i]["toggle"]["rich_text"][0]["text"]["content"], "Démonstration")

    def test_the_remark_in_parentheses_of_a_statement_is_kept(self):
        # Regression: a model once gave a definition without "(or infinity if no largest integer exists)".
        # The page must at least keep every word it is given.
        texte = " ".join(texte_du_bloc(b) for b in self.blocs if b["type"] == "paragraph")
        self.assertIn("(or infinity if no largest value exists)", texte)

    def test_an_empty_overview_gives_no_overview_block(self):
        blocs = construire_page(fiche(fil_conducteur="  "))[2]
        self.assertEqual(titres(blocs, "heading_2")[0], "Partie 1 : Penalising least squares")

    def test_a_label_goes_in_front_of_the_first_paragraph(self):
        blocs = blocs_avec_etiquette("Explication", "First line.\nSecond line.")
        self.assertEqual([etiquette(b) for b in blocs], ["Explication : ", None])

    def test_an_empty_text_gives_no_block_and_no_label(self):
        self.assertEqual(blocs_avec_etiquette("Explication", ""), [])
        self.assertEqual(blocs_avec_etiquette("Explication", "   "), [])

    def test_a_full_first_paragraph_keeps_the_label_on_its_own_line(self):
        texte = " ".join(f"$x_{i}$ t" for i in range(60))  # more than 100 rich_text items in one line
        blocs = blocs_avec_etiquette("Explication", texte)
        self.assertEqual(texte_du_bloc(blocs[0]), "Explication : ")
        self.assertTrue(all(len(b["paragraph"]["rich_text"]) <= 100 for b in blocs))

    def test_a_statement_of_equations_is_not_given_a_label(self):
        self.assertEqual([b["type"] for b in creer_blocs_avec_maths("$$a = b$$")], ["equation"])


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.prompt = prompts.prompt_cours("TEXTE DU COURS")

    def test_the_course_text_comes_last(self):
        self.assertTrue(self.prompt.endswith("TEXTE DU COURS"))

    def test_the_method_asks_for_a_slide_by_slide_reading_and_the_logical_flow(self):
        self.assertIn("diapositive par diapositive", self.prompt)
        self.assertIn("dans quel but il la montre", self.prompt)
        self.assertIn("le fil conducteur du cours", self.prompt)

    def test_it_asks_for_a_few_parts_each_with_an_introduction(self):
        self.assertIn("Découpe le cours en quelques parties", self.prompt)
        self.assertIn("de 3 à 8", self.prompt)
        self.assertIn(CONSIGNE_INTRODUCTION_PARTIE, self.prompt)

    def test_it_asks_for_no_paragraph_per_slide_but_for_no_content_lost(self):
        self.assertIn("PAS un élément par diapositive", self.prompt)
        self.assertIn("le contenu de CHAQUE diapositive doit se retrouver", self.prompt)

    def test_it_lists_every_type_of_element(self):
        for type_element in ("Définition", "Théorème", "Proposition", "Lemme", "Corollaire",
                             "Concept clé", "Formule", "Algorithme", "Exemple"):
            self.assertIn(type_element, self.prompt)

    def test_it_asks_for_the_explanation_of_the_formulas(self):
        self.assertIn(CONSIGNE_EXPLICATION, self.prompt)
        self.assertIn("ce que chaque symbole", CONSIGNE_EXPLICATION)
        self.assertIn("pas une simple reformulation", CONSIGNE_EXPLICATION)
        self.assertIn("exemple concret tiré du cours", CONSIGNE_EXPLICATION)

    def test_it_asks_for_the_statement_to_be_copied_in_full(self):
        self.assertIn("recopié EN ENTIER, sans rien couper", self.prompt)
        self.assertIn("remarques entre parenthèses", self.prompt)

    def test_it_shows_the_level_of_explanation_expected_with_an_example_not_to_copy(self):
        self.assertIn("EXEMPLE DU NIVEAU D'EXPLICATION ATTENDU", self.prompt)
        self.assertIn("ne le recopie pas", self.prompt)
        self.assertIn("dimension VC", self.prompt)

    def test_it_makes_the_mathematical_notation_the_first_priority(self):
        self.assertIn("RIGUEUR MATHÉMATIQUE (priorité absolue", self.prompt)
        self.assertIn("Reprends EXACTEMENT les notations", self.prompt)
        self.assertIn("Ne remplace jamais une notation du cours", self.prompt)

    def test_it_tells_the_model_not_to_guess_a_formula_that_is_scrambled(self):
        self.assertIn("ne la devine pas", self.prompt)
        self.assertIn("formule à vérifier dans le support", self.prompt)

    def test_it_forbids_inventing_content(self):
        self.assertIn("N'invente aucun concept", self.prompt)

    def test_it_warns_about_drawn_figures_and_repeated_slides(self):
        self.assertIn("Ne les recopie pas", self.prompt)
        self.assertIn("ne les compte qu'une fois", self.prompt)

    def test_it_no_longer_asks_for_the_three_labels_of_each_element(self):
        for ancien in ("pourquoi_dans_le_cours", "explication_simple", "En clair", "Formellement"):
            self.assertNotIn(ancien, self.prompt)

    def test_it_keeps_the_rules_shared_by_every_prompt(self):
        self.assertIn(CONSIGNE_FORMULES, self.prompt)
        self.assertIn("Écris bien \\neq pour différent", self.prompt)

    def test_the_example_of_the_prompt_is_itself_free_of_formulas_outside_dollars(self):
        # The example shows the model what clean formulas look like: it must follow its own rule.
        exemple = self.prompt[self.prompt.index("Énoncé : la dimension VC"):self.prompt.index("RIGUEUR MATHÉMATIQUE")]
        from latex_cleaner import latex_hors_dollars
        self.assertEqual(latex_hors_dollars(exemple), [])


class FormulaCheckTest(unittest.TestCase):
    def modifier(self, partie, element, champ, valeur):
        parties = fakes.cours_answer()["parties"]
        if element is None:
            parties[partie][champ] = valeur
        else:
            parties[partie]["elements"][element][champ] = valeur
        return fiche(parties=parties)

    def test_the_clean_sample_is_not_flagged(self):
        self.assertEqual(gemini_client.formules_hors_dollars(fiche()), [])

    def test_raw_latex_in_an_explanation_is_flagged(self):
        sheet = self.modifier(0, 0, "explication", "The penalty is lambda times h_{ij} with 0 \\leq h_{ij} .")
        self.assertTrue(gemini_client.formules_hors_dollars(sheet))

    def test_raw_latex_in_the_introduction_of_a_part_is_flagged(self):
        sheet = self.modifier(1, None, "introduction", "It bounds the error by \\frac{d}{n} in the next slide.")
        self.assertTrue(gemini_client.formules_hors_dollars(sheet))

    def test_raw_latex_in_the_overview_is_flagged(self):
        self.assertTrue(gemini_client.formules_hors_dollars(fiche(fil_conducteur="We study X^{-1} and more.")))


if __name__ == "__main__":
    unittest.main()
