"""The four processing steps (courses, exercises, labs, notes) and their order."""
import os
import shutil

import settings
from code_checks import verifier_code_lab
from gemini_client import documents_pour_gemini, fichiers_televerses, generer_fiche
from notion_api import envoyer_vers_notion, recuperer_dernier_cours_notion
from prompts import (
    contexte_cours_officiel,
    prompt_cours,
    prompt_exercices,
    prompt_lab,
    prompt_linter,
    prompt_session,
)
from schemas import FicheCours, FicheExercices, FicheLab, FicheNotesManuscrites
from settings import log
from sources import (
    extraire_date_photo,
    identifier_creneau,
    tronquer,
)


def corriger_formatage_notes(fiche: FicheNotesManuscrites) -> FicheNotesManuscrites:
    """Second pass: ask Gemini to clean the layout of a note. On failure the first version is kept."""
    log("Contrôle qualité du formatage (Passe 2 Linter)...")
    try:
        return generer_fiche(prompt_linter(fiche.model_dump_json()), FicheNotesManuscrites)
    except Exception as e:
        log(f"Avertissement Passe 2 (Linter) : {e}. Conservation de la fiche initiale.")
        return fiche


def traiter_cours():
    """Each PDF in the Cours folder becomes a "Cours fiché" page."""
    d = settings.dossiers()
    fichiers_cours = [f for f in os.listdir(d.cours) if f.endswith(".pdf")]
    for fichier in fichiers_cours:
        chemin = os.path.join(d.cours, fichier)
        try:
            with documents_pour_gemini([("Cours", chemin)]) as (parties, textes):
                fiche = generer_fiche(parties + [prompt_cours(textes["Cours"])], FicheCours)
            envoyer_vers_notion(fiche, settings.NOTION_DATABASE_ID)
            shutil.move(chemin, os.path.join(d.archives, fichier))
            log(f"Cours '{fichier}' traité et archivé avec succès.\n")
        except Exception as e:
            log(f"Échec sur le cours '{fichier}' : {e}. Fichier laissé en place.\n")


def traiter_exercices():
    """Each exercise sheet that has a correction becomes a "PC" page."""
    d = settings.dossiers()
    fichiers_exos = [f for f in os.listdir(d.exos) if f.endswith(".pdf")]
    for fichier_exo in fichiers_exos:
        chemin_exo = os.path.join(d.exos, fichier_exo)
        nom_base = os.path.splitext(fichier_exo)[0]
        candidats_correction = [
            os.path.join(d.corriges, f"{nom_base}_Correction.pdf"),
            os.path.join(d.corriges, f"{nom_base}_correction.pdf"),
            os.path.join(d.corriges, fichier_exo)
        ]
        chemin_corrige = next((c for c in candidats_correction if os.path.exists(c)), None)
        if not chemin_corrige:
            continue
        try:
            with documents_pour_gemini([("Énoncé", chemin_exo), ("Corrigé", chemin_corrige)]) as (parties, textes):
                fiche = generer_fiche(parties + [prompt_exercices(textes["Énoncé"], textes["Corrigé"])], FicheExercices)
            envoyer_vers_notion(fiche, settings.NOTION_DATABASE_ID)
            shutil.move(chemin_exo, os.path.join(d.archives, fichier_exo))
            shutil.move(chemin_corrige, os.path.join(d.archives, os.path.basename(chemin_corrige)))
            log(f"Exercices '{fichier_exo}' traités et archivés avec succès.\n")
        except Exception as e:
            log(f"Échec sur les exercices '{fichier_exo}' : {e}. Fichiers laissés en place.\n")


def traiter_labs():
    """Each lab that has a correction becomes a "Lab" page."""
    d = settings.dossiers()
    if not os.path.exists(d.labs):
        return
    fichiers_labs = sorted(
        f for f in os.listdir(d.labs)
        if any(f.lower().endswith(ext) for ext in settings.EXT_LABS) and not f.startswith((".", "~"))
    )
    for fichier_lab in fichiers_labs:
        chemin_lab = os.path.join(d.labs, fichier_lab)
        base = os.path.splitext(fichier_lab)[0]

        # Fixed search order (extensions sorted)
        candidats = [
            os.path.join(d.labs_corriges, f"{nom}{ext}")
            for nom in (f"{base}_Correction", f"{base}_correction", base)
            for ext in sorted(settings.EXT_LABS)
        ]
        chemin_corrige = next((c for c in candidats if os.path.exists(c)), None)
        if not chemin_corrige:
            log(f"Attente du corrigé pour le Lab '{fichier_lab}'...")
            continue

        try:
            log(f"Lab : {fichier_lab} (corrigé : {os.path.basename(chemin_corrige)})")
            with documents_pour_gemini([("Énoncé du lab", chemin_lab), ("Corrigé du lab", chemin_corrige)]) as (parties, textes):
                enonce, corrige = textes["Énoncé du lab"], textes["Corrigé du lab"]
                if enonce is not None:
                    enonce = tronquer(enonce, settings.LIMITE_LAB_CARS, f"Énoncé de '{fichier_lab}'")
                if corrige is not None:
                    corrige = tronquer(corrige, settings.LIMITE_LAB_CARS, f"Corrigé de '{os.path.basename(chemin_corrige)}'")
                fiche = generer_fiche(parties + [prompt_lab(enonce, corrige)], FicheLab)
            verifier_code_lab(fiche, corrige or "")
            envoyer_vers_notion(fiche, settings.NOTION_DATABASE_ID, categorie_forcee="Lab")
            shutil.move(chemin_lab, os.path.join(d.archives, fichier_lab))
            shutil.move(chemin_corrige, os.path.join(d.archives, os.path.basename(chemin_corrige)))
            log(f"Lab '{fichier_lab}' traité et archivé avec succès.\n")
        except Exception as e:
            log(f"Échec sur le Lab '{fichier_lab}' : {e}. Fichiers laissés en place.\n")


def traiter_session(session_id: str, infos: dict):
    """One session of notes (the photos taken in the same slot) becomes one page."""
    d = settings.dossiers()
    fichiers_session = infos["fichiers"]
    cours_impose = infos["cours"]
    cat_imposee = infos["cat"]
    log(f"Traitement de la session '{session_id}' ({len(fichiers_session)} fichier(s))...")

    cours_officiel = None
    if cours_impose:
        log(f"Recherche de la dernière fiche 'Cours fiché' pour '{cours_impose}' dans Notion...")
        cours_officiel = recuperer_dernier_cours_notion(cours_impose)
        if cours_officiel:
            log(f"Lien établi avec le cours officiel Notion : '{cours_officiel['titre']}'")
        else:
            log(f"Aucune fiche récente trouvée dans Notion pour '{cours_impose}'. Mode autonome.")
    contexte = contexte_cours_officiel(cours_officiel)

    fichiers_session.sort(key=lambda p: extraire_date_photo(p))

    # Pass 1: the sheet, written from the photos (uploaded for this request only), pass 2: layout clean-up
    with fichiers_televerses(fichiers_session) as photos:
        fiche_obtenue = generer_fiche(photos + [prompt_session(cours_impose, contexte)], FicheNotesManuscrites)
    fiche_nettoyee = corriger_formatage_notes(fiche_obtenue)

    if cours_impose:
        fiche_nettoyee.nom_du_cours_notion = cours_impose

    envoyer_vers_notion(fiche_nettoyee, settings.NOTION_DATABASE_ID, categorie_forcee=cat_imposee, reference_cours=cours_officiel)

    for p in fichiers_session:
        shutil.move(p, os.path.join(d.archives, os.path.basename(p)))
    log(f"Session '{session_id}' traitée et archivée avec succès.\n")


def traiter_notes():
    """Group the photos by timetable slot, then process each session.

    A photo whose name has no date is left in place, because its slot cannot be known.
    """
    d = settings.dossiers()
    extensions_images = ('.png', '.jpg', '.jpeg', '.heic', '.pdf')
    fichiers_notes = [f for f in os.listdir(d.notes) if f.lower().endswith(extensions_images)]

    sessions = {}
    for f in fichiers_notes:
        chemin = os.path.join(d.notes, f)
        date_photo = extraire_date_photo(chemin)
        if date_photo is None:
            log(f"Date introuvable dans le nom de '{f}' (format attendu : AAAA-MM-JJ_HH-MM-SS). Fichier laissé en place.")
            continue
        cours_identifie, categorie_identifiee, session_id = identifier_creneau(date_photo)
        if session_id not in sessions:
            sessions[session_id] = {
                "cours": cours_identifie,
                "cat": categorie_identifiee,
                "date": date_photo,
                "fichiers": []
            }
        sessions[session_id]["fichiers"].append(chemin)

    for session_id, infos in sessions.items():
        try:
            traiter_session(session_id, infos)
        except Exception as e:
            log(f"Échec sur la session '{session_id}' : {e}. Fichiers laissés en place.\n")


def lancer_pipeline():
    """Create the folders if needed, then run the four steps in order."""
    log("Vérification des dossiers Google Drive...")
    d = settings.dossiers()
    for dossier in [d.cours, d.exos, d.corriges, d.labs, d.labs_corriges, d.notes, d.archives]:
        os.makedirs(dossier, exist_ok=True)

    traiter_cours()
    traiter_exercices()
    traiter_labs()
    traiter_notes()
