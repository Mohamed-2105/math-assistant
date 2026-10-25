"""Reading the input: files on disk, the date of a photo (from its name) and the timetable slot it falls in."""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import settings
from settings import log

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


def identifier_creneau(dt: datetime):
    """(course, category, session id) of a moment, from the timetable in settings.PLANNING."""
    jour = dt.weekday()
    heure_minute = (dt.hour, dt.minute)
    for c in settings.PLANNING:
        if c["jour"] == jour and c["debut"] <= heure_minute <= c["fin"]:
            identifiant_session = f"{dt.strftime('%Y-%m-%d')}_{c['cours']}_{c['categorie']}"
            return c["cours"], c["categorie"], identifiant_session
    identifiant_session = f"{dt.strftime('%Y-%m-%d')}_SessionLibre"
    return None, "Notes de cours", identifiant_session


def extraire_date_photo(chemin_fichier: str) -> Optional[datetime]:
    """Date of a photo, read from its file name only (YYYY-MM-DD_HH-MM-SS).

    Returns None when the name holds no valid date. Photo metadata is not used.
    """
    nom_fichier = os.path.basename(chemin_fichier)
    match = re.search(r'(\d{4}-\d{2}-\d{2})_(\d{2})[:\.-](\d{2})[:\.-](\d{2})', nom_fichier)
    if not match:
        return None
    try:
        chaine_date = f"{match.group(1)} {match.group(2)}:{match.group(3)}:{match.group(4)}"
        return datetime.strptime(chaine_date, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def pdf_vers_markdown(chemin: str) -> str:
    """Text of a PDF as markdown. The library is imported here so that the other modules do not need it.

    OCR is switched off unless PDF_AVEC_OCR is True. A version of the library that does not know the
    use_ocr option is called without it.
    """
    import pymupdf4llm
    if not settings.PDF_AVEC_OCR:
        try:
            return pymupdf4llm.to_markdown(chemin, use_ocr=False)
        except TypeError:
            pass
    return pymupdf4llm.to_markdown(chemin)


def extraire_contenu_fichier(chemin: str) -> str:
    """Text of a course, lab or exercise file (.pdf, .ipynb, .rmd, .html or plain text)."""
    path = Path(chemin)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return pdf_vers_markdown(str(path))

    if ext == ".ipynb":
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            log(f"Avertissement : Notebook illisible : {path.name}")
            return ""
        # Language read from the notebook metadata (python by default)
        lang = str(data.get("metadata", {}).get("language_info", {}).get("name", "python")).lower().strip()
        morceaux = []
        for cell in data.get("cells", []):
            source = "".join(cell.get("source", []))
            if not source.strip():
                continue
            if cell.get("cell_type") == "markdown":
                morceaux.append(f"\n[Explications / Enoncé] :\n{source}\n")
            elif cell.get("cell_type") == "code":
                morceaux.append(f"\n```{lang}\n{source}\n```\n")
        return "\n".join(morceaux)

    contenu = path.read_text(encoding="utf-8", errors="ignore")
    if ext == ".rmd":
        if contenu.startswith("---"):
            parts = contenu.split("---", 2)
            if len(parts) >= 3:
                contenu = parts[2]
        return contenu

    if ext == ".html":
        if BeautifulSoup:
            soup = BeautifulSoup(contenu, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            return soup.get_text(separator="\n")
        return re.sub(r"<[^>]+>", "\n", contenu)
    return contenu


def tronquer(texte: str, limite: int, etiquette: str) -> str:
    if len(texte) > limite:
        log(f"Avertissement : {etiquette} tronqué : {len(texte)} -> {limite} caractères.")
        return texte[:limite]
    return texte
