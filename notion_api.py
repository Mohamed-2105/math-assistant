"""Calls to the Notion API: read the latest course sheet, create a page, append blocks."""
from typing import Optional

import httpx

import settings
from katex_validator import verifier_formules
from notion_blocks import construire_page, convertir_equations_en_texte
from settings import log


def headers():
    return {
        "Authorization": f"Bearer {settings.NOTION_TOKEN}",
        "Notion-Version": settings.NOTION_VERSION,
        "Content-Type": "application/json"
    }


def recuperer_dernier_cours_notion(nom_cours: str) -> Optional[dict]:
    """Latest "Cours fiché" page of a course, with some of its text, or None."""
    payload = {
        "filter": {
            "and": [
                {"property": "Cours", "select": {"equals": nom_cours}},
                {"property": "Catégorie", "select": {"equals": "Cours fiché"}}
            ]
        },
        "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        "page_size": 1
    }
    try:
        r = httpx.post(f"https://api.notion.com/v1/databases/{settings.NOTION_DATABASE_ID}/query", headers=headers(), json=payload, timeout=20.0)
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
        if not results:
            return None
        page = results[0]
        page_id = page["id"]

        titre = "Cours officiel"
        for p_name, p_val in page.get("properties", {}).items():
            if p_val.get("type") == "title" and p_val.get("title"):
                titre = p_val["title"][0]["plain_text"]
                break

        r_blocks = httpx.get(f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100", headers=headers(), timeout=20.0)
        contenu_texte = []
        if r_blocks.status_code == 200:
            for b in r_blocks.json().get("results", []):
                b_type = b.get("type")
                if b_type in b:
                    rich_texts = b[b_type].get("rich_text", [])
                    texte_ligne = "".join([t.get("plain_text", "") for t in rich_texts])
                    if texte_ligne:
                        contenu_texte.append(texte_ligne)
        return {
            "titre": titre,
            "url": page.get("url", ""),
            "contenu": "\n".join(contenu_texte[:40])
        }
    except Exception as e:
        log(f"Avertissement lors de la récupération du cours dans Notion : {e}")
        return None


def ajouter_blocs(page_id: str, blocs):
    """Append blocks to a page. On an error, retry once with the equations turned into text."""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    r = httpx.patch(url, headers=headers(), json={"children": blocs}, timeout=30.0)
    if r.status_code != 200:
        log(f"Avertissement patch Notion (Code {r.status_code}) : {r.text}. Nouvel essai avec équations en texte...")
        convertir_equations_en_texte(blocs)
        r = httpx.patch(url, headers=headers(), json={"children": blocs}, timeout=30.0)
        if r.status_code != 200:
            log(f"Avertissement : échec définitif de l'ajout de blocs : {r.text}")


def envoyer_vers_notion(fiche, database_id, categorie_forcee=None, reference_cours=None):
    """Create the Notion page of a sheet. Raises RuntimeError if the sheet is empty or if Notion refuses it every time."""
    if fiche.est_vide():
        raise RuntimeError("Fiche vide : aucune page n'est créée dans Notion.")
    nom_cours = fiche.nom_du_cours_notion
    titre_page, type_notion_valeur, children_blocks = construire_page(fiche, categorie_forcee, reference_cours)
    verifier_formules(children_blocks)

    r_db = httpx.get(f"https://api.notion.com/v1/databases/{database_id}", headers=headers(), timeout=20.0)
    props_schema = r_db.json().get("properties", {}) if r_db.status_code == 200 else {}

    nom_col_titre = "title"
    proprietes_page = {}

    for col_name, col_info in props_schema.items():
        c_type = col_info.get("type")
        if c_type == "title":
            nom_col_titre = col_name
        elif col_name.strip().lower() == "cours":
            if c_type == "select":
                proprietes_page[col_name] = {"select": {"name": nom_cours}}
            elif c_type == "multi_select":
                proprietes_page[col_name] = {"multi_select": [{"name": nom_cours}]}
        elif col_name.strip().lower() in ["catégorie", "categorie", "type"]:
            if c_type == "select":
                proprietes_page[col_name] = {"select": {"name": type_notion_valeur}}
            elif c_type == "multi_select":
                proprietes_page[col_name] = {"multi_select": [{"name": type_notion_valeur}]}

    proprietes_page[nom_col_titre] = {"title": [{"text": {"content": titre_page}}]}

    payload = {
        "parent": {"database_id": database_id},
        "properties": proprietes_page,
        "children": children_blocks[:100]
    }

    r_create = httpx.post("https://api.notion.com/v1/pages", headers=headers(), json=payload, timeout=30.0)

    # Fallback 1: equations turned into text everywhere, properties kept
    if r_create.status_code != 200:
        log(f"Avertissement Notion (Code {r_create.status_code}) : {r_create.text}. Fallback 1 : équations converties en texte...")
        convertir_equations_en_texte(children_blocks)
        payload["children"] = children_blocks[:100]
        r_create = httpx.post("https://api.notion.com/v1/pages", headers=headers(), json=payload, timeout=30.0)

    # Fallback 2: properties reduced to the title, in case a property is the problem
    if r_create.status_code != 200:
        log(f"Avertissement Notion (Code {r_create.status_code}) : {r_create.text}. Fallback 2 : propriétés réduites au titre...")
        payload["properties"] = {nom_col_titre: {"title": [{"text": {"content": titre_page}}]}}
        r_create = httpx.post("https://api.notion.com/v1/pages", headers=headers(), json=payload, timeout=30.0)

    if r_create.status_code != 200:
        raise RuntimeError(f"Création de la page Notion impossible (code {r_create.status_code}) : {r_create.text}")

    nouvelle_page = r_create.json()

    for i in range(100, len(children_blocks), 100):
        ajouter_blocs(nouvelle_page["id"], children_blocks[i:i + 100])

    log(f"Fiche creee dans Notion : {titre_page} -> {nouvelle_page.get('url', 'URL indisponible')}")
