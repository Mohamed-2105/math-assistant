"""Tests of the LaTeX cleaning. Each test comes from a real failure met during the work."""
import unittest

from latex_cleaner import (
    SENTINELLE_PRIX,
    decouper_equation,
    equilibrer_accolades,
    latex_hors_dollars,
    reparer_latex_et_sauts,
    valider_et_nettoyer_latex,
)
from notion_blocks import creer_blocs_avec_maths, texte_vers_rich_text_latex
from tests.fakes import ENONCES_LATEX_BRUT


def a_plat(rich_text):
    """Readable form of a rich_text: the text as is and each equation between < and >."""
    return "".join(
        e["text"]["content"] if e["type"] == "text" else f"<{e['equation']['expression']}>"
        for e in rich_text
    )


class FormulaAndPriceTest(unittest.TestCase):
    def test_formulas_that_start_with_a_digit_are_not_prices(self):
        texte = a_plat(texte_vers_rich_text_latex("Soit $2n$ pair, $1$ impair, $0 \\leq x$ et $2 + x$."))
        self.assertEqual(texte.count("<"), 4)
        self.assertNotIn("$", texte)

    def test_prices_stay_text(self):
        texte = a_plat(texte_vers_rich_text_latex("Ça coûte $50 et $60 ici, soit $x$ au total."))
        self.assertIn("$50 et $60", texte)
        self.assertEqual(texte.count("<"), 1)


class DamagedCommandTest(unittest.TestCase):
    def test_commands_that_start_with_n_are_restored(self):
        # In an ordinary string "\n" is a line break: this is what a JSON reader gives
        # when the model forgets to double the backslash.
        brut = "Soit $a\neq b$, $\nabla f$ et $x \neg y$"
        texte = a_plat(texte_vers_rich_text_latex(brut))
        for attendu in ("a\\neq b", "\\nabla f", "x \\neg y"):
            self.assertIn(attendu, texte)

    def test_control_characters_become_latex_commands_again(self):
        brut = "$\x08eta + \x0crac{1}{2} + \theta \times \rho$"
        texte = a_plat(texte_vers_rich_text_latex(brut))
        for attendu in ("\\beta", "\\frac{1}{2}", "\\theta \\times \\rho"):
            self.assertIn(attendu, texte)

    def test_a_real_line_break_before_u_in_a_display_formula_is_kept(self):
        blocs = creer_blocs_avec_maths("$$\\begin{aligned} a &= b \\\\\n u &= c \\end{aligned}$$")
        self.assertNotIn("\\nu", blocs[0]["equation"]["expression"])

    def test_a_literal_backslash_n_outside_a_formula_is_a_line_break(self):
        self.assertIn("\n", reparer_latex_et_sauts("ligne1\\nLigne2"))

    def test_list_environments_are_removed(self):
        texte = reparer_latex_et_sauts("\\begin{itemize}\\item a \\item b\\end{itemize}")
        self.assertNotIn("\\item", texte)
        self.assertNotIn("itemize", texte)


class BraceTest(unittest.TestCase):
    def test_escaped_braces_are_not_counted(self):
        expr = valider_et_nettoyer_latex(r"f(x)=\left\{ \begin{array}{ll} 1 & x>0 \\ 0 & x\le 0 \end{array} \right.")
        self.assertTrue(expr.endswith("\\right."))

    def test_a_missing_brace_is_added(self):
        self.assertEqual(equilibrer_accolades(r"\frac{1}{2"), r"\frac{1}{2}")


class LongEquationTest(unittest.TestCase):
    def test_a_long_sum_is_split_without_losing_anything(self):
        expr = " + ".join(f"\\frac{{a_{i}}}{{b_{i}}}" for i in range(200))
        morceaux = decouper_equation(expr)
        self.assertGreater(len(morceaux), 1)
        for morceau in morceaux:
            self.assertLessEqual(len(morceau), 1000)
            self.assertEqual(morceau.count("{"), morceau.count("}"))
        self.assertEqual("".join(morceaux).replace(" ", ""), expr.replace(" ", ""))

    def test_a_long_aligned_block_is_split_by_rows(self):
        lignes = " \\\\ ".join(f"x_{{{i}}} &= a_{{{i}}} + b_{{{i}}} + c_{{{i}}} + d_{{{i}}}" for i in range(80))
        morceaux = decouper_equation("\\begin{aligned} " + lignes + " \\end{aligned}")
        self.assertGreater(len(morceaux), 1)
        for morceau in morceaux:
            self.assertLessEqual(len(morceau), 1000)
            self.assertTrue(morceau.startswith("\\begin{aligned}"))
            self.assertTrue(morceau.endswith("\\end{aligned}"))

    def test_a_short_equation_is_left_alone(self):
        self.assertEqual(decouper_equation("a + b"), ["a + b"])


class OutsideDollarsTest(unittest.TestCase):
    def test_a_formula_between_dollars_is_not_reported(self):
        self.assertEqual(latex_hors_dollars("Let $h_{ij} = (P_X)_{ij}$ and $x \\leq y$."), [])

    def test_raw_latex_like_on_the_notion_page_is_reported(self):
        # Regression: Gemini wrote formulas without dollar signs, so Notion showed \leq and h_{ij} as text.
        brut = ("Consider P_X = X(X^\\top X)^{-1}X^\\top . Let h_{ij} = (P_X)_{ij} , the entries.\n"
                "Then 0 \\leq h_{ii} \\leq 1 .")
        self.assertGreaterEqual(len(latex_hors_dollars(brut)), 4)

    def test_only_the_raw_part_of_a_mixed_text_is_reported(self):
        extraits = latex_hors_dollars("We have $x^2$ and also y^{2} here.")
        self.assertEqual(len(extraits), 1)
        self.assertIn("y^{2}", extraits[0])

    def test_a_command_damaged_by_json_outside_dollars_is_reported_as_the_command(self):
        self.assertTrue(latex_hors_dollars("a\neq b"))  # a line break then "eq", restored to \neq

    def test_ordinary_text_is_not_reported(self):
        for texte in ("Use fit_transform then predict_proba.", "The pattern `\\d+` matches digits.",
                      "It costs $50 per run and $60 in total.", "A plain sentence.", ""):
            with self.subTest(text=texte):
                self.assertEqual(latex_hors_dollars(texte), [])

    def test_a_bare_subscript_is_a_known_blind_spot(self):
        # P_X looks like a variable name, so it is not reported. Documented in the README.
        self.assertEqual(latex_hors_dollars("The projector P_X is symmetric."), [])


class RawLatexRepairTest(unittest.TestCase):
    """LaTeX that a model wrote without dollar signs is put between dollars."""

    ENONCES = ENONCES_LATEX_BRUT

    def test_a_statement_that_is_a_bare_formula_becomes_one_formula(self):
        for enonce in self.ENONCES:
            with self.subTest(statement=enonce[:30]):
                self.assertEqual(reparer_latex_et_sauts(enonce), f"${enonce}$")

    def test_the_repaired_statement_is_an_equation_in_the_blocks(self):
        for enonce in self.ENONCES:
            with self.subTest(statement=enonce[:30]):
                rich_text = creer_blocs_avec_maths(enonce)[0]["paragraph"]["rich_text"]
                self.assertEqual([item["type"] for item in rich_text], ["equation"])
                self.assertEqual(rich_text[0]["equation"]["expression"], enonce)

    def test_the_words_of_a_sentence_stay_and_only_its_formula_is_wrapped(self):
        texte = "Let h_{ij} = (P_X)_{ij} , the entries of P_X . The trace is equal to :"
        self.assertEqual(reparer_latex_et_sauts(texte),
                         "Let $h_{ij} = (P_X)_{ij}$, the entries of $P_X$. The trace is equal to :")

    def test_the_punctuation_after_a_formula_stays_outside_it(self):
        self.assertEqual(reparer_latex_et_sauts("We get \\alpha = 1."), "We get $\\alpha = 1$.")

    def test_a_list_marker_stays_outside_the_formula(self):
        self.assertEqual(reparer_latex_et_sauts("(1) 0 \\leq h_{ii} \\leq 1 ."), "(1) $0 \\leq h_{ii} \\leq 1$.")
        self.assertEqual(reparer_latex_et_sauts("(a) \\alpha is the step"), "(a) $\\alpha$ is the step")
        self.assertEqual(reparer_latex_et_sauts("- \\alpha controls the step"), "- $\\alpha$ controls the step")

    def test_a_formula_inside_a_sentence_does_not_swallow_the_prose_next_to_it(self):
        self.assertEqual(reparer_latex_et_sauts("where \\alpha is a positive constant"), "where $\\alpha$ is a positive constant")

    def test_a_simple_subscript_is_a_formula(self):
        self.assertEqual(reparer_latex_et_sauts("The projector P_X is symmetric."), "The projector $P_X$ is symmetric.")

    def test_prose_and_code_are_left_alone(self):
        for texte in ("The model is fitted by least squares on the training data.",
                      "Use fit_transform then predict_proba on x_train.",
                      "Use `\\\\frac{a}{b}` as written in the file.",
                      "It costs $50 per run and $60 in total.",
                      "(or infinity if no largest value exists)."):
            with self.subTest(text=texte):
                self.assertEqual(reparer_latex_et_sauts(texte).replace(SENTINELLE_PRIX, "$"), texte)  # a price keeps its marker until the text is written

    def test_a_formula_that_already_has_its_dollars_is_not_wrapped_twice(self):
        texte = "Let $\\lambda > 0$ and $x_i \\leq 1$, with 0 \\leq p ."
        self.assertEqual(reparer_latex_et_sauts(texte), "Let $\\lambda > 0$ and $x_i \\leq 1$, with $0 \\leq p$.")

    def test_a_display_formula_with_its_dollars_is_left_alone(self):
        texte = "$$\n\\hat\\beta = (X^T X)^{-1} X^T y\n$$"
        self.assertEqual(reparer_latex_et_sauts(texte), texte)

    def test_the_repair_is_idempotent(self):
        for texte in self.ENONCES + ["Let h_{ij} = (P_X)_{ij} , the entries of P_X ."]:
            with self.subTest(text=texte[:30]):
                une_fois = reparer_latex_et_sauts(texte)
                self.assertEqual(reparer_latex_et_sauts(une_fois), une_fois)

    def test_a_repaired_formula_is_cleaned_like_any_other(self):
        # A missing brace is added, as for a formula that came with its dollars.
        self.assertEqual(reparer_latex_et_sauts("We use \\frac{1}{2"), "We use $\\frac{1}{2}$")

    def test_the_detection_still_sees_the_raw_latex_before_the_repair(self):
        for enonce in self.ENONCES:
            with self.subTest(statement=enonce[:30]):
                self.assertTrue(latex_hors_dollars(enonce))

    def test_after_the_repair_there_is_nothing_left_outside_the_dollars(self):
        for enonce in self.ENONCES:
            propre = reparer_latex_et_sauts(enonce)
            self.assertEqual(latex_hors_dollars(propre), [])

    def test_the_dollars_of_a_repaired_text_are_balanced(self):
        for enonce in self.ENONCES:
            self.assertEqual(reparer_latex_et_sauts(enonce).count("$") % 2, 0)

    def test_a_raw_line_in_a_short_text_becomes_an_inline_equation(self):
        rich_text = texte_vers_rich_text_latex("Then \\sum_{i=1}^n h_{ii} = p")
        self.assertEqual([item["type"] for item in rich_text], ["text", "equation"])


if __name__ == "__main__":
    unittest.main()
