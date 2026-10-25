"""Cleaning of the LaTeX written by the model so that Notion accepts it.

This module works on text only: no file and no network.
"""
import re
from typing import List

from settings import LIMITE_EQUATION

# A "$" followed by a number is a price only if the number is followed (after an optional
# unit) by a word, a punctuation mark or the end of the line. "$2n$", "$1$", "$0 \leq x$"
# and "$2 + x$" are formulas, not prices.
PRIX_RE = re.compile(
    r'\$(?=\d+(?:[.,]\d+)*\s?(?:[kKM]|bn|USD|€|euros?|dollars?)?[.,;:!?)]*(?:\s+[A-Za-zÀ-ÿ]{2,}|\s*$))',
    re.M
)
SENTINELLE_PRIX = "\u0000PRIX\u0000"

# Control characters produced when a LaTeX "\b", "\f", "\a" or "\v" is read as a JSON escape
CARS_CONTROLE = {'\x07': '\\a', '\x08': '\\b', '\x0b': '\\v', '\x0c': '\\f'}

# LaTeX commands that start with "\n": when the "\n" was read as a line break, it is restored.
# The "sure" list is applied everywhere. The "ambiguous" list only inside inline formulas.
CMD_N_SURES = r'(?:eq|abla|otin|eg|leq|geq|subseteq|supseteq|exists|rightarrow|leftarrow|leftrightarrow|parallel|cong)'
CMD_N_AMBIGUES = r'(?:u|e|i|ot|mid|sim|less|gtr|atural|ewline)'


def equilibrer_accolades(expr: str) -> str:
    """Balance the braces, ignoring escaped braces and double backslashes."""
    sans_echappees = re.sub(r'\\\\', '', expr)
    sans_echappees = re.sub(r'\\[{}]', '', sans_echappees)
    ouvrantes = sans_echappees.count('{')
    fermantes = sans_echappees.count('}')
    if ouvrantes > fermantes:
        expr += '}' * (ouvrantes - fermantes)
    elif fermantes > ouvrantes:
        expr = '{' * (fermantes - ouvrantes) + expr
    return expr


def valider_et_nettoyer_latex(expr: str) -> str:
    """Guards against KaTeX errors in Notion: lone dollars, unsupported environments, braces."""
    if not expr:
        return ""
    expr = expr.strip()

    # 1. Escape lone "$" inside a formula
    expr = re.sub(r'(?<!\\)\$', r'\\$', expr)

    # 2. Replace environments KaTeX does not support
    expr = expr.replace(r'\begin{align}', r'\begin{aligned}').replace(r'\end{align}', r'\end{aligned}')
    expr = expr.replace(r'\begin{align*}', r'\begin{aligned}').replace(r'\end{align*}', r'\end{aligned}')
    expr = expr.replace(r'\begin{tabular}', r'\begin{matrix}').replace(r'\end{tabular}', r'\end{matrix}')

    # Line breaks inside a formula become spaces
    expr = re.sub(r'\s+', ' ', expr)

    # 3. Balance the braces
    return equilibrer_accolades(expr)


def reparer_commandes_corrompues(texte: str, dans_formule: bool = False, display: bool = False) -> str:
    """Restore LaTeX commands damaged by JSON reading ("\\n" followed by "eq" became a line break)."""
    texte = re.sub(r'\n(?=' + CMD_N_SURES + r'(?![a-zA-Z]))', lambda m: '\\n', texte)
    if dans_formule:
        texte = re.sub(r'\t(?=[a-zA-Z])', lambda m: '\\t', texte)
        texte = re.sub(r'\r(?=[a-zA-Z])', lambda m: '\\r', texte)
        if not display:
            texte = re.sub(r'(?<!\\)\n(?=' + CMD_N_AMBIGUES + r'(?![a-zA-Z]))', lambda m: '\\n', texte)
        texte = re.sub(r'(?<=[=+\-\(\[\s])eta\b', lambda m: '\\beta', texte)
        texte = re.sub(r'\\b(?![a-zA-Z])', lambda m: '\\beta ', texte)
    return texte


def reparer_latex_et_sauts(texte: str, delimiter_formules_brutes: bool = True) -> str:
    """Repair a text that mixes prose and formulas, before it is turned into Notion blocks.

    With `delimiter_formules_brutes`, LaTeX that the model wrote outside the dollar signs is put between dollars,
    so that it is cleaned like any other formula and shown as a formula. The detection of that LaTeX turns it off.
    """
    if not texte:
        return ""

    texte = texte.replace('\r\n', '\n')

    # Prices (for example "$50 et") become a marker, restored when the final text is written
    texte = PRIX_RE.sub(SENTINELLE_PRIX, texte)

    # Control characters become LaTeX commands (\beta, \frac, \alpha, \vec)
    for car, cmd in CARS_CONTROLE.items():
        texte = texte.replace(car, cmd)

    # List environments are not supported: turn them into plain lines
    texte = re.sub(r'\\begin\{(itemize|enumerate)\}', '', texte)
    texte = re.sub(r'\\end\{(itemize|enumerate)\}', '\n', texte)
    texte = re.sub(r'\\item', '\n- ', texte)
    texte = re.sub(r'\*\*(.*?)\*\*', r'\1', texte)

    # Protect the formulas (repaired from the inside) while the prose is cleaned
    formules = []

    def proteger_formule(m):
        brut = m.group(0)
        f = reparer_commandes_corrompues(brut, dans_formule=True, display=brut.startswith('$$'))
        formules.append(f.replace('\n', ' '))
        return f"__MATH_FORMULA_{len(formules)-1}__"

    texte = re.sub(r'\$\$[\s\S]*?\$\$', proteger_formule, texte)
    texte = re.sub(r'\$[^\$]+?\$', proteger_formule, texte)

    # Outside formulas: damaged commands, then literal "\n" become real line breaks
    texte = reparer_commandes_corrompues(texte)
    texte = re.sub(r'\\n(?!(?:' + CMD_N_SURES + '|' + CMD_N_AMBIGUES + r')(?![a-zA-Z]))', '\n', texte)

    # LaTeX written outside the dollar signs: it becomes a formula like the others
    if delimiter_formules_brutes:
        def mettre_en_formule(expression):
            formules.append(f"${expression}$")
            return f"__MATH_FORMULA_{len(formules) - 1}__"

        texte = "\n".join(delimiter_ligne_brute(ligne, mettre_en_formule) for ligne in texte.split("\n"))

    # Prose clean-up
    texte = re.sub(r'-\s*Postulat\s*', '\n', texte)
    texte = re.sub(r'(?<=\.)\s*(\[P?[0-9a-zA-Z]+\])', r'\n\1', texte)
    texte = re.sub(r'(?<=\))\s*(\[P?[0-9a-zA-Z]+\])', r'\n\1', texte)
    texte = re.sub(r'\n\s*-\s*\n', '\n', texte)

    # Put the cleaned formulas back
    for i, f in enumerate(formules):
        if f.startswith('$$') and f.endswith('$$'):
            expr_interne = valider_et_nettoyer_latex(f[2:-2])
            f_propre = f"$$\n{expr_interne}\n$$"
        elif f.startswith('$') and f.endswith('$'):
            expr_interne = valider_et_nettoyer_latex(f[1:-1])
            f_propre = f"${expr_interne}$"
        else:
            f_propre = f
        texte = texte.replace(f"__MATH_FORMULA_{i}__", f_propre)

    return texte.strip()


# --- Splitting of long equations (Notion limit: 1000 characters) ---
def positions_sures(expr: str):
    """Positions where a formula can be cut safely (outside braces, \\left/\\right and environments)."""
    ops, espaces = [], []
    acc = env = lr = 0
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c == '\\':
            m = re.match(r'\\(begin|end)\{[^}]*\}', expr[i:])
            if m:
                env += 1 if m.group(1) == 'begin' else -1
                i += m.end()
                continue
            m = re.match(r'\\(left|right)(?![a-zA-Z])', expr[i:])
            if m:
                lr += 1 if m.group(1) == 'left' else -1
                i += m.end()
                continue
            i += 2
            continue
        if c == '{':
            acc += 1
        elif c == '}':
            acc -= 1
        elif acc == 0 and env == 0 and lr == 0:
            if c in '=+-' and i > 0 and expr[i - 1] not in '^_(':
                ops.append(i)
            elif c == ' ':
                espaces.append(i)
        i += 1
    return ops, espaces


def decouper_generique(expr: str, limite: int) -> List[str]:
    morceaux = []
    reste = expr.strip()
    while len(reste) > limite:
        ops, espaces = positions_sures(reste)
        coupe = max([p for p in ops if 0 < p <= limite], default=None)
        if coupe is None:
            coupe = max([p for p in espaces if 0 < p <= limite], default=None)
        if coupe is None:
            coupe = limite  # last resort: a raw cut
        morceaux.append(reste[:coupe].strip())
        reste = reste[coupe:].strip()
    if reste:
        morceaux.append(reste)
    return morceaux


def decouper_equation(expr: str, limite: int = LIMITE_EQUATION) -> List[str]:
    """Split an equation longer than the limit into several valid equations (by aligned row if possible)."""
    expr = expr.strip()
    if len(expr) <= limite:
        return [expr]

    def envelopper(lignes):
        return r'\begin{aligned} ' + r' \\ '.join(lignes) + r' \end{aligned}'

    m = re.fullmatch(r'\\begin\{aligned\}([\s\S]*)\\end\{aligned\}', expr)
    if m:
        lignes = [l.strip() for l in re.split(r'\\\\', m.group(1)) if l.strip()]
        morceaux, courant = [], []
        marge = limite - len(envelopper(['']))
        for l in lignes:
            if len(envelopper([l])) > limite:
                if courant:
                    morceaux.append(envelopper(courant))
                    courant = []
                for p in decouper_generique(l, marge):
                    morceaux.append(envelopper([p]))
            elif courant and len(envelopper(courant + [l])) > limite:
                morceaux.append(envelopper(courant))
                courant = [l]
            else:
                courant.append(l)
        if courant:
            morceaux.append(envelopper(courant))
        return [equilibrer_accolades(x) for x in morceaux]

    return [equilibrer_accolades(p) for p in decouper_generique(expr, limite)]


# --- Detection of LaTeX written outside the dollar signs ---
# A LaTeX command of two letters or more (\leq, \frac, \top...), or a subscript or superscript with
# braces (h_{ij}, X^{-1}). A bare subscript such as P_X is not detected: it looks like a variable name.
SIGNAL_LATEX = re.compile(r"\\[A-Za-z]{2,}|[A-Za-z0-9)\]}]_\{|\^\{")


# Words of a line that can belong to a formula: a token with a LaTeX character, a single letter (a variable),
# or a token made of digits and operators. A token with a backtick is code, not a formula.
_MARQUEURS = re.compile(r"__MATH_FORMULA_\d+__|\x00PRIX\x00")
_LETTRE_ISOLEE = re.compile(r"^[(\[|]*[A-Za-z][0-9']*[)\]|,.;:']*$")
_SYMBOLES = re.compile(r"^[0-9=+\-*/<>≤≥≠∈∞()\[\]|,.;:']+$")
# A list marker at the start of a run stays outside the formula: (1) 1. 1) [P1] - * •
_ENUMERATEUR = re.compile(r"^(?:\(?\d+[.)]|\([a-z]\)|\[P?\d+\]|[-*•])$")
# A subscript of one letter or one digit on a single letter (P_X, x_i, X_1) is a formula too, but it is too
# short to be a clear sign of LaTeX on its own in the detection: only the repair uses it.
_INDICE_SIMPLE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]_[A-Za-z0-9](?![A-Za-z0-9_])")


def _est_un_mot_de_formule(mot: str) -> bool:
    if "`" in mot or _MARQUEURS.search(mot):
        return False
    if any(caractere in mot for caractere in "\\_^{}"):
        return True
    return bool(_LETTRE_ISOLEE.match(mot) or _SYMBOLES.match(mot))


def delimiter_ligne_brute(ligne: str, mettre_en_formule) -> str:
    """Put between dollars the LaTeX that a line holds outside any dollar sign.

    The line is cut into words. A series of words that can belong to a formula is turned into a formula
    when it holds a clear sign of LaTeX (see SIGNAL_LATEX). A list marker at its start and the punctuation at
    its end stay outside. `mettre_en_formule(expression)` gives what replaces the expression in the line.
    Prose is left alone, so a sentence with a formula in the middle keeps its words.
    """
    mots = list(re.finditer(r"\S+", ligne))
    sortie, position, i = [], 0, 0
    while i < len(mots):
        if not _est_un_mot_de_formule(mots[i].group()):
            i += 1
            continue
        j = i
        while j + 1 < len(mots) and _est_un_mot_de_formule(mots[j + 1].group()):
            j += 1
        debut = i
        while debut <= j and _ENUMERATEUR.match(mots[debut].group()):
            debut += 1
        if debut <= j:
            serie = ligne[mots[debut].start():mots[j].end()]
            if SIGNAL_LATEX.search(serie) or _INDICE_SIMPLE.search(serie):
                expression = serie.rstrip(".,;:")
                sortie.append(ligne[position:mots[debut].start()])
                sortie.append(mettre_en_formule(expression.strip()) + serie[len(expression):])
                position = mots[j].end()
        i = j + 1
    sortie.append(ligne[position:])
    return "".join(sortie)


def latex_hors_dollars(texte: str) -> List[str]:
    """Short extracts of a text where LaTeX appears outside $...$ and $$...$$.

    Notion shows such LaTeX as plain text. The text goes through the usual repair first, so that a
    command damaged by JSON reading is seen as the command it was. Inline code between backticks,
    which may hold a regular expression or a path, is left out.
    """
    if not texte:
        return []
    propre = reparer_latex_et_sauts(texte, delimiter_formules_brutes=False)
    sans_formules = re.sub(r"\$\$[\s\S]*?\$\$|\$[^\$]+?\$", " ", propre)
    sans_formules = re.sub(r"`[^`]*`", " ", sans_formules).replace(SENTINELLE_PRIX, " ")
    extraits = []
    for trouve in SIGNAL_LATEX.finditer(sans_formules):
        debut, fin = max(0, trouve.start() - 15), min(len(sans_formules), trouve.end() + 15)
        extraits.append(" ".join(sans_formules[debut:fin].split()))
    return extraits
