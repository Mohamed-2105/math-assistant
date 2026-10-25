"""Diagnostic of the Gemini models: do they send a summary of their thoughts, and how long are they silent?

Nothing in your Drive and nothing in Notion is touched. Only Gemini is called.

    python diagnostic_gemini.py                   a small question to each model of GEMINI_MODELS
    python diagnostic_gemini.py --pdf cours.pdf   the real request of a course, stopped at the first piece of the answer
    python diagnostic_gemini.py --modele NOM      one model only
    python diagnostic_gemini.py --lister          the models that your key can use
    python diagnostic_gemini.py --lister --filtre pro    only the names that contain "pro"

A model that sends no summary may still be thinking: it just does not say so.
"""
import argparse
import os
import sys
import threading
import time

import gemini_client
import prompts
import settings
from schemas import FicheCours
from settings import log

QUESTION = "Combien y a-t-il de nombres premiers entre 100 et 150 ? Réfléchis soigneusement, puis donne la liste et le total."


def observer(modele, contents, config, arreter_a_la_reponse=False):
    """Read one streamed answer and note what arrives and when.

    Returns (events, usage). An event is (seconds since the request, "pensées" or "réponse", characters).
    """
    debut = time.monotonic()
    derniere = [debut]
    arret = threading.Event()

    def veiller():
        while not arret.wait(settings.INTERVALLE_PROGRES):
            silence = time.monotonic() - derniere[0]
            if silence >= settings.INTERVALLE_PROGRES:
                log(f"   {time.monotonic() - debut:6.1f} s : rien reçu depuis {round(silence)} s")

    fil = threading.Thread(target=veiller, daemon=True)
    fil.start()
    evenements, usage, vus = [], None, set()
    flux = gemini_client.get_client().models.generate_content_stream(model=modele, contents=contents, config=config)
    try:
        for morceau in flux:
            maintenant = time.monotonic()
            derniere[0] = maintenant
            usage = getattr(morceau, "usage_metadata", None) or usage
            texte = len(morceau.text or "")
            for genre, taille in (("pensées", gemini_client._taille_pensees(morceau)), ("réponse", texte)):
                if taille:
                    evenements.append((maintenant - debut, genre, taille))
                    if genre not in vus:
                        vus.add(genre)
                        log(f"   {maintenant - debut:6.1f} s : premier morceau de {genre} ({taille} caractères)")
            if arreter_a_la_reponse and texte:
                break
    finally:
        arret.set()
        fil.join(timeout=1)
        fermer = getattr(flux, "close", None)
        if fermer:
            fermer()
    return evenements, usage


def resumer(evenements, usage=None):
    """The lines that sum up what a model did."""
    pensees = [e for e in evenements if e[1] == "pensées"]
    reponses = [e for e in evenements if e[1] == "réponse"]
    if pensees:
        lignes = [f"envoie des résumés de pensées : {len(pensees)} morceau(x), le premier à {pensees[0][0]:.1f} s"]
    else:
        lignes = ["n'envoie aucun résumé de pensées (il peut quand même réfléchir sans le dire)"]
    lignes.append(f"premier morceau de réponse à {reponses[0][0]:.1f} s" if reponses else "aucune réponse reçue")
    jetons_reflexion = getattr(usage, "thoughts_token_count", None)
    if jetons_reflexion:
        lignes.append(f"jetons de réflexion annoncés par Gemini : {jetons_reflexion}")
    jetons_envoyes = getattr(usage, "prompt_token_count", None)
    if jetons_envoyes:
        lignes.append(f"jetons envoyés à Gemini : {jetons_envoyes}")
    return lignes


def modeles_disponibles(filtre=None):
    """The models that the key can use to write text, as (name, model) pairs sorted by name.

    The name is the one to put in GEMINI_MODELS. `filtre` keeps the names that contain a word.
    """
    trouves = []
    for modele in gemini_client.get_client().models.list():
        if "generateContent" not in (modele.supported_actions or []):
            continue
        nom = (modele.name or "").removeprefix("models/")
        if filtre and filtre.lower() not in nom.lower():
            continue
        trouves.append((nom, modele))
    return sorted(trouves, key=lambda paire: paire[0])


def lister(filtre=None):
    """Print the models that the key can use, and mark the ones that are in GEMINI_MODELS."""
    modeles = modeles_disponibles(filtre)
    if not modeles:
        log("Aucun modèle trouvé" + (f" avec « {filtre} » dans son nom." if filtre else "."))
        return
    log(f"{len(modeles)} modèle(s) utilisables pour écrire du texte" + (f" (nom contenant « {filtre} »)" if filtre else "") + " :\n")
    for nom, modele in modeles:
        marque = "   <- dans GEMINI_MODELS" if nom in settings.GEMINI_MODELS else ""
        details = []
        if modele.input_token_limit:
            details.append(f"entrée max {modele.input_token_limit} jetons")
        if modele.output_token_limit:
            details.append(f"sortie max {modele.output_token_limit} jetons")
        if modele.thinking is not None:
            details.append("réfléchit" if modele.thinking else "ne réfléchit pas")
        log(f"{nom}{marque}")
        log(f"   {modele.display_name or ''} | {' | '.join(details)}")
    log("\nPour utiliser un modèle, écris son nom dans GEMINI_MODELS du fichier .env (le meilleur en premier, séparés par des virgules).")


def diagnostiquer_un_modele(modele, contents, config, arreter_a_la_reponse):
    log(f"\n=== {modele} ===")
    reflexion = gemini_client.config_reflexion(modele)
    if reflexion is not None:
        config = {**config, "thinking_config": reflexion}
    try:
        evenements, usage = observer(modele, contents, config, arreter_a_la_reponse)
    except Exception as e:
        if gemini_client._est_une_erreur_de_reflexion(e):
            log("   refuse les réglages de réflexion (RESUME_PENSEES, NIVEAU_REFLEXION, BUDGET_REFLEXION) : le programme les retire pour ce modèle")
        elif gemini_client.est_un_delai_depasse(e):
            log(f"   plus rien reçu pendant {settings.DELAI_APPEL_GEMINI} s : le modèle est abandonné")
        else:
            log(f"   erreur : {e}")
        return
    for ligne in resumer(evenements, usage):
        log("   " + ligne)


def diagnostiquer(modeles, pdf=None):
    """Run the diagnostic on each model. With a PDF, the request is the real one of a course sheet."""
    log(f"Réglages lus : RESUME_PENSEES={settings.RESUME_PENSEES}, NIVEAU_REFLEXION={settings.NIVEAU_REFLEXION or 'défaut du modèle'}, "
        f"BUDGET_REFLEXION={settings.BUDGET_REFLEXION if settings.BUDGET_REFLEXION is not None else 'défaut du modèle'}, "
        f"DELAI_APPEL_GEMINI={settings.DELAI_APPEL_GEMINI} s, DUREE_MAX_REPONSE={settings.DUREE_MAX_REPONSE} s")
    if pdf is None:
        log(f"Question posée à chaque modèle : {QUESTION}")
        for modele in modeles:
            diagnostiquer_un_modele(modele, QUESTION, {"temperature": 0.0}, arreter_a_la_reponse=False)
        return
    log(f"PDF : {os.path.basename(pdf)}. La demande est celle d'une vraie fiche de cours, arrêtée au premier morceau de réponse.")
    with gemini_client.fichiers_televerses([pdf]) as (fichier,):
        contents = ["Cours (PDF joint) :", fichier, prompts.prompt_cours(None)]
        config = {"response_mime_type": "application/json", "response_schema": FicheCours, "temperature": 0.0}
        for modele in modeles:
            diagnostiquer_un_modele(modele, contents, config, arreter_a_la_reponse=True)


def main(argv=None):
    parseur = argparse.ArgumentParser(description="Diagnostic of the Gemini models.")
    parseur.add_argument("--pdf", help="path of a course PDF: test the real request of a course sheet")
    parseur.add_argument("--modele", help="test this model only (default: every model of GEMINI_MODELS)")
    parseur.add_argument("--lister", action="store_true", help="list the models that your key can use, then stop")
    parseur.add_argument("--filtre", help="with --lister: keep the names that contain this word")
    args = parseur.parse_args(argv)
    if not settings.GEMINI_API_KEY:
        log("GEMINI_API_KEY est vide : remplis le fichier .env.")
        return 1
    if args.lister:
        lister(args.filtre)
        return 0
    if args.pdf and not os.path.isfile(os.path.expanduser(args.pdf)):
        log(f"PDF introuvable : {args.pdf}")
        return 1
    pdf = os.path.expanduser(args.pdf) if args.pdf else None
    diagnostiquer([args.modele] if args.modele else settings.GEMINI_MODELS, pdf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
