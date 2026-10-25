"""Checks on the code that Gemini writes for a lab.

They only warn, the page is created anyway:
- fidelity: the code lines of the correction must be found in the code Gemini wrote;
- syntax: the Python code must be valid Python (R code is not checked).
Both are heuristics. They tell you where to look, they do not prove that the code is right.
"""
import ast
import re
import textwrap

import settings
from settings import log

# Lines that Gemini is asked to remove ("redundant imports, display tests, warnings") or that carry
# no code: they are left out of the comparison. Lines that start with % or ! are notebook magics.
IGNOREES = re.compile(
    r"^(#|import\s|from\s+\S+\s+import\s|library\(|require\(|suppressPackageStartupMessages\("
    r"|print\(|cat\(|display\(|plt\.show\(|warnings\.|[%!])"
)


def normaliser(ligne: str) -> str:
    """A line without spaces and with one kind of quote, so that reformatting does not count as a change."""
    return re.sub(r"\s+", "", ligne).replace('"', "'")


def est_significative(ligne: str) -> bool:
    ligne = ligne.strip()
    return bool(ligne) and not IGNOREES.match(ligne) and re.search(r"[A-Za-z0-9]", ligne) is not None


def lignes_de_code(texte: str):
    """The lines inside fenced code blocks (``` ... ```) of a text."""
    lignes, dans_un_bloc = [], False
    for ligne in texte.splitlines():
        if ligne.strip().startswith("```"):
            dans_un_bloc = not dans_un_bloc
            continue
        if dans_un_bloc:
            lignes.append(ligne)
    return lignes


def verifier_fidelite(code_genere: str, texte_corrige: str):
    """Compare the code of the correction with the code Gemini wrote.

    Returns None when the correction has no code block to compare with. Otherwise returns
    (lines found, lines expected, lines missing). A line is found when it appears in the code
    Gemini wrote, ignoring spaces and the kind of quote, even if Gemini merged or split lines.
    """
    attendues = {}
    for ligne in lignes_de_code(texte_corrige):
        if est_significative(ligne):
            attendues.setdefault(normaliser(ligne), ligne.strip())
    if not attendues:
        return None
    tout = "".join(normaliser(l) for l in code_genere.splitlines())
    absentes = [original for norme, original in attendues.items() if norme not in tout]
    return len(attendues) - len(absentes), len(attendues), absentes


def erreur_syntaxe_python(code: str):
    """None if the code is valid Python, else a short message. Notebook magics are ignored."""
    texte = "\n".join(re.sub(r"^(\s*)[%!].*$", r"\1pass", ligne) for ligne in textwrap.dedent(code).splitlines())
    try:
        ast.parse(texte)
    except SyntaxError as e:
        return f"{e.msg} (ligne {e.lineno})"
    return None


def verifier_code_lab(fiche, texte_corrige: str):
    """Run both checks on a lab sheet, print the warnings and return them as a list."""
    avertissements = []
    for partie in fiche.parties:
        if partie.langage_code == "python":
            erreur = erreur_syntaxe_python(partie.code_solution_propre)
            if erreur:
                avertissements.append(
                    f"Avertissement : code python invalide dans la question '{partie.titre_question}' : {erreur}.")

    code_genere = "\n".join(p.code_solution_propre for p in fiche.parties)
    resultat = verifier_fidelite(code_genere, texte_corrige)
    if resultat is None:
        log("Contrôle de fidélité du code ignoré : aucun bloc de code repéré dans le corrigé.")
    else:
        trouvees, total, absentes = resultat
        if trouvees / total < settings.SEUIL_FIDELITE_CODE:
            exemples = " ; ".join(ligne[:60] for ligne in absentes[:5])
            reste = f" (+{len(absentes) - 5} autres)" if len(absentes) > 5 else ""
            avertissements.append(
                f"Avertissement : le code du lab '{fiche.titre_lab}' est peut-être incomplet : "
                f"{trouvees} lignes de code sur {total} du corrigé retrouvées ({round(100 * trouvees / total)} %). "
                f"Lignes absentes : {exemples}{reste}.")

    for avertissement in avertissements:
        log(avertissement)
    return avertissements
