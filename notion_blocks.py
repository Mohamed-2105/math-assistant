"""Building of Notion blocks from a sheet. No network: the result is plain data."""
import re
from typing import List

import settings
from latex_cleaner import (
    SENTINELLE_PRIX,
    decouper_equation,
    reparer_latex_et_sauts,
    valider_et_nettoyer_latex,
)
from settings import TAILLE_TOGGLE


def decouper(texte: str, taille: int) -> List[str]:
    return [texte[i:i + taille] for i in range(0, len(texte), taille)]


def nettoyer_titre(titre: str, limite: int = None) -> str:
    """Title of a Notion page: one line of plain text, at most `limite` characters.

    The dollar signs are removed because a page title cannot display formulas. A title that is
    too long is cut at the last word that fits and ends with an ellipsis. An empty title becomes
    "Sans titre". The default limit is LIMITE_TITRE in settings.py.
    """
    limite = limite or settings.LIMITE_TITRE
    titre = " ".join(titre.replace("$", "").split())
    if not titre:
        return "Sans titre"
    if len(titre) <= limite:
        return titre
    coupe = titre[:limite - 1]
    if " " in coupe and len(coupe.rsplit(" ", 1)[0]) >= limite // 2:
        coupe = coupe.rsplit(" ", 1)[0]
    return coupe.rstrip(" ,;:-") + "…"


def texte_vers_rich_text_latex(texte: str):
    """Full list of rich_text items for a line (never truncated). Callers split it by 100 if needed."""
    texte = reparer_latex_et_sauts(texte)
    rich_text = []
    parties = re.split(r'(\$[^\$]+?\$)', texte)
    for partie in parties:
        if not partie:
            continue
        if partie.startswith('$') and partie.endswith('$') and len(partie) > 2:
            expression_latex = valider_et_nettoyer_latex(partie[1:-1])
            if expression_latex:
                for morceau_eq in decouper_equation(expression_latex):
                    rich_text.append({"type": "equation", "equation": {"expression": morceau_eq}})
        else:
            partie = partie.replace(SENTINELLE_PRIX, '$')
            for m in decouper(partie, 1900):
                if m.strip():
                    rich_text.append({"type": "text", "text": {"content": m}})
    return rich_text or [{"type": "text", "text": {"content": " "}}]


def rt_titre(texte: str):
    """rich_text for a title or a short line (Notion limit: 100 items)."""
    return texte_vers_rich_text_latex(texte)[:100]


def blocs_depuis_rich_text(rich_text, type_bloc: str = "paragraph"):
    """Spread a rich_text of more than 100 items over several blocks, losing nothing."""
    return [
        {"object": "block", "type": type_bloc, type_bloc: {"rich_text": rich_text[i:i + 100]}}
        for i in range(0, len(rich_text), 100)
    ]


def blocs_code(code: str, langage: str):
    """Code block(s): one block per 100 rich_text items (190 000 characters), nothing is cut."""
    morceaux = [{"type": "text", "text": {"content": c}} for c in decouper(code, 1900)]
    if not morceaux:
        morceaux = [{"type": "text", "text": {"content": " "}}]
    return [
        {"object": "block", "type": "code", "code": {"language": langage, "rich_text": morceaux[i:i + 100]}}
        for i in range(0, len(morceaux), 100)
    ]


def creer_toggles(titre: str, blocs, taille: int = TAILLE_TOGGLE):
    """Toggle(s) holding the blocks. Past `taille` children, "(suite n)" toggles are added instead of cutting."""
    toggles = []
    for n, i in enumerate(range(0, len(blocs), taille)):
        libelle = titre if n == 0 else f"{titre} (suite {n})"
        toggles.append({
            "object": "block", "type": "toggle",
            "toggle": {
                "rich_text": [{"type": "text", "text": {"content": libelle}, "annotations": {"bold": True, "italic": True}}],
                "children": blocs[i:i + taille]
            }
        })
    return toggles


def creer_blocs_avec_maths(texte: str):
    """Blocks for a text: one paragraph per line and one equation block per $$...$$."""
    texte = reparer_latex_et_sauts(texte)
    blocs = []
    segments = re.split(r'(\$\$[\s\S]*?\$\$)', texte)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if seg.startswith('$$') and seg.endswith('$$') and len(seg) > 4:
            expr = valider_et_nettoyer_latex(seg[2:-2])
            if expr:
                for morceau_eq in decouper_equation(expr):
                    blocs.append({"object": "block", "type": "equation", "equation": {"expression": morceau_eq}})
        else:
            for ligne in seg.split('\n'):
                ligne = ligne.strip()
                if not ligne or ligne == '-':
                    continue
                if ligne.startswith('- '):
                    ligne = ligne[2:].strip()
                blocs.extend(blocs_depuis_rich_text(texte_vers_rich_text_latex(ligne)))
    return blocs


def collecter_equations(noeud):
    """Every equation of a list of blocks, at any depth, as (expression, display) pairs without duplicates.

    display is True for an equation block and False for an equation inside a text.
    """
    trouvees = []

    def parcourir(n):
        if isinstance(n, list):
            for element in n:
                parcourir(element)
        elif isinstance(n, dict):
            if n.get("object") == "block" and n.get("type") == "equation":
                cle = (n["equation"]["expression"], True)
                if cle not in trouvees:
                    trouvees.append(cle)
                return
            if n.get("type") == "equation" and "equation" in n and "object" not in n:
                cle = (n["equation"]["expression"], False)
                if cle not in trouvees:
                    trouvees.append(cle)
                return
            for valeur in n.values():
                parcourir(valeur)

    parcourir(noeud)
    return trouvees


def blocs_avec_etiquette(etiquette: str, texte: str):
    """Blocks for a text, introduced by a bold label such as "En clair : ".

    The label is put at the start of the first paragraph. When the text starts with an equation, or
    when the first paragraph is already full, the label gets a paragraph of its own. An empty text
    gives no block at all.
    """
    blocs = creer_blocs_avec_maths(texte)
    if not blocs:
        return []
    etiquette_rt = {"type": "text", "text": {"content": f"{etiquette} : "}, "annotations": {"bold": True}}
    premier = blocs[0]
    if premier["type"] == "paragraph" and len(premier["paragraph"]["rich_text"]) < 100:
        premier["paragraph"]["rich_text"].insert(0, etiquette_rt)
    else:
        blocs.insert(0, {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [etiquette_rt]}})
    return blocs


def convertir_equations_en_texte(noeud, seulement=None):
    """Turn equations (block or inline, at any depth) into plain text, in place.

    `seulement` is an optional set of (expression, display) pairs: only these are converted.
    """
    if isinstance(noeud, list):
        for el in noeud:
            convertir_equations_en_texte(el, seulement)
    elif isinstance(noeud, dict):
        if noeud.get("object") == "block" and noeud.get("type") == "equation":
            expr = noeud["equation"]["expression"]
            if seulement is not None and (expr, True) not in seulement:
                return
            noeud.clear()
            noeud.update({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"$$ {expr} $$"}}]}
            })
            return
        if noeud.get("type") == "equation" and "equation" in noeud and "object" not in noeud:
            expr = noeud["equation"]["expression"]
            if seulement is not None and (expr, False) not in seulement:
                return
            noeud.clear()
            noeud.update({"type": "text", "text": {"content": f"${expr}$"}})
            return
        for v in list(noeud.values()):
            convertir_equations_en_texte(v, seulement)


def construire_page(fiche, categorie_forcee=None, reference_cours=None):
    """Turn a sheet into (page title, Notion category, list of blocks)."""
    children_blocks = []
    titre_page = ""
    type_notion_valeur = categorie_forcee

    if fiche.type_fiche == "Cours":
        titre_page = fiche.titre_cours
        type_notion_valeur = type_notion_valeur or "Cours fiché"
        if fiche.fil_conducteur.strip():
            children_blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt_titre("Vue d'ensemble")}})
            children_blocks.extend(creer_blocs_avec_maths(fiche.fil_conducteur))
            children_blocks.append({"object": "block", "type": "divider", "divider": {}})
        for numero, partie in enumerate(fiche.parties, 1):
            children_blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt_titre(f"Partie {numero} : {partie.titre_partie}")}})
            children_blocks.extend(blocs_avec_etiquette("Dans cette partie", partie.introduction))
            for el in partie.elements:
                children_blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": rt_titre(f"{el.type_element} — {el.nom}")}})
                children_blocks.extend(creer_blocs_avec_maths(el.enonce_latex))
                children_blocks.extend(blocs_avec_etiquette("Explication", el.explication))
                if el.demonstration_utile and el.demonstration_latex:
                    blocs_demo = creer_blocs_avec_maths(el.demonstration_latex)
                    if blocs_demo:
                        children_blocks.extend(creer_toggles("Démonstration", blocs_demo))
                children_blocks.append({"object": "block", "type": "divider", "divider": {}})

    elif fiche.type_fiche == "Exercices":
        titre_page = fiche.titre_sujet
        type_notion_valeur = type_notion_valeur or "PC"

        children_blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt_titre("Exercices et Corrections Détaillées")}})
        for ex in fiche.exercices:
            blocs_correction = creer_blocs_avec_maths(ex.correction_etofee_latex)
            children_blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": rt_titre(ex.numero_exo)}})
            children_blocks.extend(creer_blocs_avec_maths(ex.enonce_latex))
            if blocs_correction:
                children_blocks.extend(creer_toggles("Correction Détaillée", blocs_correction))
            children_blocks.append({"object": "block", "type": "divider", "divider": {}})

        children_blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt_titre("Méthodes et Réflexes à retenir")}})
        for m in fiche.methode_a_retenir:
            children_blocks.extend(blocs_depuis_rich_text(texte_vers_rich_text_latex(m), "bulleted_list_item"))

    elif fiche.type_fiche == "Lab":
        titre_page = fiche.titre_lab
        type_notion_valeur = type_notion_valeur or "Lab"

        children_blocks.append({
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": f"TP & Implémentation — Langage : {fiche.langage_principal.upper()}"}}],
                "color": "blue_background",
            }
        })
        for p in fiche.parties:
            children_blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt_titre(p.titre_question)}})
            children_blocks.extend(creer_blocs_avec_maths(p.enonce_ou_contexte))
            if p.justification_theorique_latex:
                blocs_theo = creer_blocs_avec_maths(p.justification_theorique_latex)
                if blocs_theo:
                    children_blocks.extend(creer_toggles("Justification Mathématique & Théorie", blocs_theo))
            children_blocks.extend(blocs_code(p.code_solution_propre.strip(), p.langage_code))

            prefix_rich = [{"type": "text", "text": {"content": "Explication de l'implémentation : "}, "annotations": {"bold": True, "italic": True}}]
            full_rich = prefix_rich + texte_vers_rich_text_latex(p.explication_code)
            children_blocks.extend(blocs_depuis_rich_text(full_rich))
            children_blocks.append({"object": "block", "type": "divider", "divider": {}})

        if fiche.reflexes_et_astuces_code:
            children_blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt_titre("Bonnes Pratiques & Astuces de Code")}})
            for a in fiche.reflexes_et_astuces_code:
                children_blocks.extend(blocs_depuis_rich_text(texte_vers_rich_text_latex(a), "bulleted_list_item"))

    elif fiche.type_fiche == "Notes":
        titre_page = fiche.titre_note
        type_notion_valeur = type_notion_valeur or "Notes de cours"

        if reference_cours:
            callout_text = f"Chapitre / Thème : {fiche.chapitre_associe} | Rattaché à : {reference_cours['titre']}"
        else:
            callout_text = f"Chapitre / Thème : {fiche.chapitre_associe} | Note autonome"

        children_blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": callout_text}}],
                "color": "gray_background"
            }
        })

        for sec in fiche.sections:
            children_blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": rt_titre(sec.titre_section)}
            })
            children_blocks.extend(creer_blocs_avec_maths(sec.contenu_latex))
            children_blocks.append({"object": "block", "type": "divider", "divider": {}})

        if fiche.remarques_importantes:
            children_blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": rt_titre("Remarques & Points clés")}
            })
            for rem in fiche.remarques_importantes:
                children_blocks.extend(blocs_depuis_rich_text(texte_vers_rich_text_latex(rem), "bulleted_list_item"))

    return nettoyer_titre(titre_page), type_notion_valeur, children_blocks
