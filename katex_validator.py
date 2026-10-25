"""Check of the equations with KaTeX, before a page is sent to Notion.

KaTeX is the library that renders equations. It runs in Node.js: katex_check.js receives all the
formulas of a page in one call. When Node.js or KaTeX is missing, the check is skipped with one
message and the program goes on. Install KaTeX with `npm install` in the folder of the project.
"""
import json
import shutil
import subprocess
from pathlib import Path

import settings
from notion_blocks import collecter_equations, convertir_equations_en_texte
from settings import log

RACINE = Path(__file__).resolve().parent
SCRIPT = RACINE / "katex_check.js"

_deja_averti = False


def katex_disponible():
    """True when the node command exists and KaTeX is installed in the project."""
    return shutil.which(settings.NODE_COMMAND) is not None and (RACINE / "node_modules" / "katex").is_dir()


def valider_formules(formules):
    """Test (expression, display) pairs with KaTeX.

    Returns a list with None for a valid formula and the error message for an invalid one, in the
    same order. Returns None when the check cannot run.
    """
    global _deja_averti
    if not formules:
        return []
    if not katex_disponible():
        if not _deja_averti:
            log("KaTeX introuvable (installe Node.js, puis lance npm install dans le dossier du projet) : formules non vérifiées.")
            _deja_averti = True
        return None
    entree = json.dumps([{"expression": expression, "display": display} for expression, display in formules])
    try:
        retour = subprocess.run([settings.NODE_COMMAND, str(SCRIPT)], input=entree, capture_output=True,
                                text=True, encoding="utf-8", timeout=settings.DELAI_KATEX)
        resultats = json.loads(retour.stdout)
        if retour.returncode != 0 or len(resultats) != len(formules):
            raise ValueError(retour.stderr.strip()[:200] or "réponse inattendue")
        return resultats
    except Exception as e:
        log(f"Avertissement : contrôle KaTeX impossible ({e}). Formules non vérifiées.")
        return None


def verifier_formules(blocs):
    """Test every equation of the blocks. A formula that KaTeX refuses becomes plain text, in place.

    Returns the refused formulas as a dict {(expression, display): error message}.
    """
    formules = collecter_equations(blocs)
    erreurs = valider_formules(formules)
    if not erreurs:
        return {}
    refusees = {formule: erreur for formule, erreur in zip(formules, erreurs) if erreur}
    for (expression, display), erreur in refusees.items():
        log(f"Formule refusée par KaTeX ({erreur[:100]}) : {expression[:80]} -> affichée en texte.")
    if refusees:
        convertir_equations_en_texte(blocs, seulement=set(refusees))
    return refusees
