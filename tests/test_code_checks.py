"""Tests of the checks on the code that Gemini writes for a lab."""
import contextlib
import io
import unittest
from unittest import mock

import settings
from code_checks import (
    erreur_syntaxe_python,
    est_significative,
    lignes_de_code,
    verifier_code_lab,
    verifier_fidelite,
)
from schemas import FicheLab
from tests import fakes

# A correction as the program reads a notebook: markdown cells, then fenced code cells.
CORRIGE = """
[Explications / Enoncé] :
Solution

```r
library(MASS)
# fit the model
fit <- lm(y ~ x, data = d)
res <- residuals(fit)
print(summary(fit))
s2 <- sum(res^2) / (n - 2)
```
"""


def lab(*parties, titre="Lab test"):
    reponse = fakes.lab_answer()
    reponse["titre_lab"] = titre
    reponse["parties"] = [
        {"titre_question": f"Question {i}", "enonce_ou_contexte": "x", "justification_theorique_latex": None,
         "langage_code": langage, "code_solution_propre": code, "explication_code": "x"}
        for i, (langage, code) in enumerate(parties, 1)
    ]
    return FicheLab.model_validate(reponse)


def verifier(fiche, corrige=CORRIGE):
    sortie = io.StringIO()
    with contextlib.redirect_stdout(sortie):
        avertissements = verifier_code_lab(fiche, corrige)
    return avertissements, sortie.getvalue()


class ReadingTheCorrectionTest(unittest.TestCase):
    def test_only_the_lines_inside_code_blocks_are_read(self):
        self.assertEqual(lignes_de_code(CORRIGE)[0], "library(MASS)")
        self.assertNotIn("Solution", lignes_de_code(CORRIGE))

    def test_a_text_without_code_block_gives_no_line(self):
        self.assertEqual(lignes_de_code("just text\nmore text"), [])

    def test_lines_that_gemini_may_remove_or_that_carry_no_code_are_left_out(self):
        for ligne in ("import numpy as np", "from sklearn import svm", "library(MASS)", "# a comment",
                      "print(x)", "cat(x)", "plt.show()", "%matplotlib inline", "})", "", "   "):
            with self.subTest(line=ligne):
                self.assertFalse(est_significative(ligne))
        for ligne in ("fit <- lm(y ~ x)", "x = np.zeros(3)", "else:"):
            with self.subTest(line=ligne):
                self.assertTrue(est_significative(ligne))


class FidelityTest(unittest.TestCase):
    def test_the_same_code_is_fully_found(self):
        trouvees, total, absentes = verifier_fidelite("fit <- lm(y ~ x, data = d)\nres <- residuals(fit)\ns2 <- sum(res^2) / (n - 2)", CORRIGE)
        self.assertEqual((trouvees, total, absentes), (3, 3, []))

    def test_spaces_and_quotes_do_not_count_as_changes(self):
        corrige = "```python\nx = foo(\"a\", 1)\n```"
        self.assertEqual(verifier_fidelite("x=foo('a',1)", corrige), (1, 1, []))

    def test_removed_imports_comments_and_prints_are_not_missing_lines(self):
        trouvees, total, _ = verifier_fidelite("fit <- lm(y ~ x, data = d)\nres <- residuals(fit)\ns2 <- sum(res^2) / (n - 2)", CORRIGE)
        self.assertEqual(total, 3)

    def test_two_lines_merged_into_one_are_still_found(self):
        trouvees, total, _ = verifier_fidelite("fit <- lm(y ~ x, data = d); res <- residuals(fit); s2 <- sum(res^2) / (n - 2)", CORRIGE)
        self.assertEqual((trouvees, total), (3, 3))

    def test_missing_lines_are_named(self):
        trouvees, total, absentes = verifier_fidelite("fit <- lm(y ~ x, data = d)", CORRIGE)
        self.assertEqual((trouvees, total), (1, 3))
        self.assertEqual(absentes, ["res <- residuals(fit)", "s2 <- sum(res^2) / (n - 2)"])

    def test_without_code_block_in_the_correction_there_is_nothing_to_compare(self):
        self.assertIsNone(verifier_fidelite("x <- 1", "Plain text correction"))

    def test_a_correction_that_has_only_ignored_lines_gives_nothing_to_compare(self):
        self.assertIsNone(verifier_fidelite("x <- 1", "```r\nlibrary(MASS)\n# comment\n```"))


class PythonSyntaxTest(unittest.TestCase):
    def test_valid_code_gives_none(self):
        self.assertIsNone(erreur_syntaxe_python("def predict(X, beta):\n    return X @ beta"))

    def test_invalid_code_gives_a_message_with_the_line(self):
        erreur = erreur_syntaxe_python("x = 1\ndef f(:\n    pass")
        self.assertIn("ligne 2", erreur)

    def test_notebook_magics_are_ignored(self):
        self.assertIsNone(erreur_syntaxe_python("%matplotlib inline\n!pip install x\nx = 1"))

    def test_an_indented_fragment_is_accepted(self):
        self.assertIsNone(erreur_syntaxe_python("    y = 2\n    z = y + 1"))


class WholeCheckTest(unittest.TestCase):
    def test_a_complete_lab_gives_no_warning(self):
        avertissements, _ = verifier(lab(("r", "fit <- lm(y ~ x, data = d)\nres <- residuals(fit)\ns2 <- sum(res^2) / (n - 2)")))
        self.assertEqual(avertissements, [])

    def test_an_incomplete_lab_is_reported_with_the_missing_lines(self):
        avertissements, affichage = verifier(lab(("r", "fit <- lm(y ~ x, data = d)"), titre="Lab 4"))
        self.assertEqual(len(avertissements), 1)
        self.assertIn("le code du lab 'Lab 4' est peut-être incomplet : 1 lignes de code sur 3 du corrigé retrouvées (33 %)", affichage)
        self.assertIn("res <- residuals(fit) ; s2 <- sum(res^2) / (n - 2)", affichage)

    def test_the_threshold_comes_from_the_settings(self):
        corrige = "```r\n" + "\n".join(f"v{i} <- {i}" for i in range(10)) + "\n```"
        fiche = lab(("r", "\n".join(f"v{i} <- {i}" for i in range(8))))  # 8 lines out of 10 are found
        self.assertEqual(verifier(fiche, corrige)[0], [])
        with mock.patch.object(settings, "SEUIL_FIDELITE_CODE", 0.9):
            self.assertEqual(len(verifier(fiche, corrige)[0]), 1)

    def test_more_than_five_missing_lines_are_summarised(self):
        corrige = "```r\n" + "\n".join(f"v{i} <- {i}" for i in range(10)) + "\n```"
        avertissements, _ = verifier(lab(("r", "v0 <- 0")), corrige)
        self.assertIn("(+4 autres)", avertissements[0])

    def test_invalid_python_is_reported_with_the_question(self):
        avertissements, _ = verifier(lab(("python", "def f(:\n    pass")), "```python\ndef f(:\n    pass\n```")
        self.assertTrue(any("code python invalide dans la question 'Question 1'" in a for a in avertissements))

    def test_r_code_is_not_checked_for_syntax(self):
        avertissements, _ = verifier(lab(("r", "def f(:\n    pass")), "```r\ndef f(:\n    pass\n```")
        self.assertEqual(avertissements, [])

    def test_a_correction_without_code_block_is_skipped_with_a_message(self):
        avertissements, affichage = verifier(lab(("r", "x <- 1")), "Plain text correction")
        self.assertEqual(avertissements, [])
        self.assertIn("Contrôle de fidélité du code ignoré", affichage)


class InThePipelineTest(unittest.TestCase):
    def test_the_warning_is_printed_and_the_lab_is_still_processed(self):
        # In the invented sample the fake model writes code that is not in the correction.
        with fakes.environment() as env:
            fakes.build_sample(env.drive)
            affichage = fakes.run_pipeline()
            archives = fakes.list_files(env.drive)
        self.assertIn("le code du lab 'Lab 1: ridge regression in R' est peut-être incomplet", affichage)
        self.assertIn("02_Archives/lab1.ipynb", archives)
        self.assertEqual(env.notion.pages_created, 8)


if __name__ == "__main__":
    unittest.main()
