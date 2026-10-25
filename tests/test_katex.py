"""Tests of the formula check with KaTeX.

Most tests use a fake check, so they need no Node.js. The tests of the class RealKatexTest run
the real KaTeX and are skipped when Node.js or KaTeX is not installed (run npm install).
"""
import contextlib
import io
import unittest
from unittest import mock

import katex_validator
import notion_api
import settings
from latex_cleaner import reparer_latex_et_sauts
from notion_blocks import collecter_equations, convertir_equations_en_texte
from schemas import FicheCours
from tests import fakes


def equation_block(expression):
    return {"object": "block", "type": "equation", "equation": {"expression": expression}}


def paragraph(*items):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": list(items)}}


def inline(expression):
    return {"type": "equation", "equation": {"expression": expression}}


def text(content):
    return {"type": "text", "text": {"content": content}}


def echantillon():
    """A new list of blocks for every test: the checks modify the blocks in place."""
    return [
        equation_block("a = 1"),
        paragraph(text("Let "), inline("x^2"), text(" and "), inline("BAD inline")),
        {"object": "block", "type": "toggle", "toggle": {"rich_text": [], "children": [
            equation_block("BAD block"),
            equation_block("a = 1"),
        ]}},
    ]


class CollectTest(unittest.TestCase):
    def test_equations_are_found_at_any_depth_with_their_mode_and_without_duplicates(self):
        self.assertEqual(collecter_equations(echantillon()), [
            ("a = 1", True), ("x^2", False), ("BAD inline", False), ("BAD block", True)])

    def test_a_selective_conversion_only_touches_the_listed_pairs(self):
        blocs = [equation_block("x"), paragraph(inline("x"))]
        convertir_equations_en_texte(blocs, seulement={("x", False)})
        self.assertEqual(blocs[0]["type"], "equation")
        self.assertEqual(blocs[1]["paragraph"]["rich_text"][0], text("$x$"))


class VerifyTest(unittest.TestCase):
    def verifier(self, blocs, markers=("BAD",)):
        fausse = fakes.FakeKatex(markers)
        sortie = io.StringIO()
        with mock.patch.object(katex_validator, "valider_formules", fausse), contextlib.redirect_stdout(sortie):
            refusees = katex_validator.verifier_formules(blocs)
        return refusees, sortie.getvalue()

    def test_refused_formulas_become_text_and_the_others_stay_equations(self):
        blocs = echantillon()
        refusees, _ = self.verifier(blocs)
        self.assertEqual(set(refusees), {("BAD inline", False), ("BAD block", True)})
        self.assertEqual(blocs[0]["type"], "equation")
        self.assertEqual(blocs[1]["paragraph"]["rich_text"][1]["type"], "equation")
        self.assertEqual(blocs[1]["paragraph"]["rich_text"][3], text("$BAD inline$"))
        enfants = blocs[2]["toggle"]["children"]
        self.assertEqual(enfants[0]["paragraph"]["rich_text"][0], text("$$ BAD block $$"))
        self.assertEqual(enfants[1]["type"], "equation")

    def test_each_refused_formula_is_named_in_the_output(self):
        _, affichage = self.verifier(echantillon())
        self.assertIn("Formule refusée par KaTeX (fake KaTeX error) : BAD block", affichage)
        self.assertIn("Formule refusée par KaTeX (fake KaTeX error) : BAD inline", affichage)

    def test_nothing_changes_when_every_formula_is_valid(self):
        blocs = [equation_block("a = 1"), paragraph(inline("x"))]
        refusees, affichage = self.verifier(blocs)
        self.assertEqual((refusees, affichage), ({}, ""))
        self.assertEqual(blocs[0]["type"], "equation")

    def test_nothing_changes_when_the_check_cannot_run(self):
        blocs = [equation_block("BAD")]
        with mock.patch.object(katex_validator, "valider_formules", lambda formules: None):
            self.assertEqual(katex_validator.verifier_formules(blocs), {})
        self.assertEqual(blocs[0]["type"], "equation")

    def test_a_formula_valid_in_one_mode_is_only_refused_in_the_mode_where_it_fails(self):
        # \begin{align} works in a block but not inside a sentence.
        def selon_le_mode(formules):
            return ["display mode only" if (not display and "align" in expression) else None
                    for expression, display in formules]

        blocs = [equation_block("align"), paragraph(inline("align"))]
        with mock.patch.object(katex_validator, "valider_formules", selon_le_mode), contextlib.redirect_stdout(io.StringIO()):
            katex_validator.verifier_formules(blocs)
        self.assertEqual(blocs[0]["type"], "equation")
        self.assertEqual(blocs[1]["paragraph"]["rich_text"][0]["type"], "text")


class SendingTest(unittest.TestCase):
    def test_a_refused_formula_is_sent_to_notion_as_text(self):
        reponse = fakes.cours_answer()
        reponse["parties"][0]["elements"][0]["enonce_latex"] = "Before.\n$$MARKER + 1$$\nAfter, with $x = 2$."
        with fakes.environment(katex=fakes.FakeKatex(["MARKER"])) as env:
            notion_api.envoyer_vers_notion(FicheCours.model_validate(reponse), settings.NOTION_DATABASE_ID)
        corps = env.notion.page_bodies[0]
        textes = [item["text"]["content"] for b in corps["children"] if b["type"] == "paragraph"
                  for item in b["paragraph"]["rich_text"] if item["type"] == "text"]
        self.assertIn("$$ MARKER + 1 $$", textes)
        equations = [b["equation"]["expression"] for b in corps["children"] if b["type"] == "equation"]
        self.assertNotIn("MARKER + 1", equations)

    def test_the_formulas_of_a_page_are_checked_in_one_call(self):
        with fakes.environment() as env:
            notion_api.envoyer_vers_notion(FicheCours.model_validate(fakes.cours_answer()), settings.NOTION_DATABASE_ID)
        self.assertEqual(len(env.katex.calls), 1)
        self.assertGreater(len(env.katex.calls[0]), 3)


class UnavailableTest(unittest.TestCase):
    def test_without_node_the_check_is_skipped_and_the_user_is_told_once(self):
        sortie = io.StringIO()
        with mock.patch.object(katex_validator.shutil, "which", return_value=None), \
                mock.patch.object(katex_validator, "_deja_averti", False), contextlib.redirect_stdout(sortie):
            premier = katex_validator.valider_formules([("x", True)])
            second = katex_validator.valider_formules([("y", True)])
        self.assertIsNone(premier)
        self.assertIsNone(second)
        self.assertEqual(sortie.getvalue().count("KaTeX introuvable"), 1)

    def test_with_nothing_to_check_nothing_is_run_and_nothing_is_printed(self):
        sortie = io.StringIO()
        with mock.patch.object(katex_validator.shutil, "which", return_value=None), contextlib.redirect_stdout(sortie):
            self.assertEqual(katex_validator.valider_formules([]), [])
        self.assertEqual(sortie.getvalue(), "")

    def test_a_failing_node_script_is_reported_and_skipped(self):
        class Retour:
            returncode = 1
            stdout = ""
            stderr = "boom"

        sortie = io.StringIO()
        with mock.patch.object(katex_validator, "katex_disponible", return_value=True), \
                mock.patch.object(katex_validator.subprocess, "run", return_value=Retour()), \
                contextlib.redirect_stdout(sortie):
            self.assertIsNone(katex_validator.valider_formules([("x", True)]))
        self.assertIn("contrôle KaTeX impossible", sortie.getvalue())


@unittest.skipUnless(katex_validator.katex_disponible(), "Node.js or KaTeX is not installed (run npm install)")
class RealKatexTest(unittest.TestCase):
    def verifier(self, *formules):
        return katex_validator.valider_formules(list(formules))

    def test_a_valid_formula_gives_none(self):
        self.assertEqual(self.verifier((r"\hat\beta = (X^T X + \lambda I)^{-1} X^T y", True)), [None])

    def test_structural_errors_are_detected(self):
        resultats = self.verifier((r"\frac{1}{2", True), (r"\left( x + y", True), (r"\foo{x}", True))
        for erreur in resultats:
            self.assertTrue(erreur)

    def test_the_results_keep_the_order_of_the_formulas(self):
        resultats = self.verifier(("a", True), (r"\foo", True), ("b", False))
        self.assertEqual([r is None for r in resultats], [True, False, True])

    def test_the_mode_matters(self):
        align = r"\begin{align} a &= b \\ c &= d \end{align}"
        self.assertEqual(self.verifier((align, True)), [None])
        self.assertTrue(self.verifier((align, False))[0])

    def test_a_command_damaged_by_json_reading_is_not_detected(self):
        # "\n" followed by "eq" is a line break then two letters: valid for KaTeX, wrong on screen.
        # Only the repair rules of latex_cleaner.py can catch this one.
        self.assertEqual(self.verifier(("a\neq b", True)), [None])

    def test_an_accented_formula_and_unicode_pass_through_node_intact(self):
        self.assertEqual(self.verifier((r"\text{Régression pénalisée } \lambda", True)), [None])

    def test_the_bare_statements_of_the_seen_page_are_accepted_by_katex_once_repaired(self):
        # These statements were shown as raw text in Notion. Once put between dollars, KaTeX reads them.
        for enonce in fakes.ENONCES_LATEX_BRUT:
            with self.subTest(statement=enonce[:30]):
                repare = reparer_latex_et_sauts(enonce)
                self.assertTrue(repare.startswith("$") and repare.endswith("$"))
                self.assertEqual(self.verifier((repare[1:-1], False)), [None])

    def test_the_whole_check_converts_a_broken_formula(self):
        blocs = [equation_block("a = 1"), equation_block(r"\frac{1}{2")]
        with contextlib.redirect_stdout(io.StringIO()):
            refusees = katex_validator.verifier_formules(blocs)
        self.assertEqual(list(refusees), [(r"\frac{1}{2", True)])
        self.assertEqual(blocs[0]["type"], "equation")
        self.assertEqual(blocs[1]["type"], "paragraph")


if __name__ == "__main__":
    unittest.main()
