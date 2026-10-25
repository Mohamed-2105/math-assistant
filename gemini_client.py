"""Access to Gemini: one client created on first use, file uploads, retries, model fallback and schema validation."""
import contextlib
import threading
import time

import httpx
from google import genai
from google.genai import errors, types

import settings
from latex_cleaner import latex_hors_dollars
from settings import log
from sources import extraire_contenu_fichier

_client = None

# Models that refused the option of the thoughts summary: it is not asked of them again during this run.
_modeles_sans_pensees = set()


def get_client():
    """The Gemini client, created on first use so that importing this module needs no key.

    Every request of this client, a prompt or a photo upload, is given up after DELAI_APPEL_GEMINI
    seconds without an answer (the SDK takes the delay in milliseconds).
    """
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=settings.DELAI_APPEL_GEMINI * 1000),
        )
    return _client


class ReponseTropLongue(TimeoutError):
    """An answer that keeps arriving for longer than DUREE_MAX_REPONSE."""


def est_un_delai_depasse(erreur):
    return isinstance(erreur, (httpx.TimeoutException, TimeoutError))


def _nom_etat(fichier):
    """PROCESSING, ACTIVE or FAILED for an uploaded file, or None when the file has no state."""
    return getattr(getattr(fichier, "state", None), "name", None)


def attendre_fichier_pret(fichier):
    """Wait until Gemini has finished reading an uploaded file. Raises an error if it fails or takes too long."""
    interrogations = max(1, round(settings.DELAI_TRAITEMENT_FICHIER / settings.INTERVALLE_SONDAGE_FICHIER))
    for _ in range(interrogations):
        if _nom_etat(fichier) != "PROCESSING":
            break
        time.sleep(settings.INTERVALLE_SONDAGE_FICHIER)
        fichier = get_client().files.get(name=fichier.name)
    if _nom_etat(fichier) == "PROCESSING":
        raise TimeoutError(f"Gemini n'a pas fini de lire le fichier '{fichier.name}' en {settings.DELAI_TRAITEMENT_FICHIER} s")
    if _nom_etat(fichier) == "FAILED":
        raise RuntimeError(f"Gemini n'a pas pu lire le fichier '{fichier.name}'")
    return fichier


def televerser_fichier(chemin):
    """Upload a file (a photo, a PDF) and wait until Gemini can use it in a prompt."""
    try:
        fichier = get_client().files.upload(file=chemin)
    except Exception as e:
        if est_un_delai_depasse(e):
            raise TimeoutError(f"pas de réponse de Gemini en {settings.DELAI_APPEL_GEMINI} s pendant l'envoi de '{chemin}'") from e
        raise
    return attendre_fichier_pret(fichier)


def supprimer_fichier(fichier):
    """Delete an uploaded file at Gemini. A failure is only reported: the file expires by itself after 48 hours."""
    nom = getattr(fichier, "name", None)
    if not nom:
        return
    try:
        get_client().files.delete(name=nom)
    except Exception as e:
        log(f"Avertissement : le fichier temporaire '{nom}' n'a pas pu être supprimé chez Gemini ({e}). Il expirera de lui-même.")


@contextlib.contextmanager
def fichiers_televerses(chemins):
    """Upload files for one request, then delete them, whatever happens in between."""
    fichiers = []
    try:
        for chemin in chemins:
            fichiers.append(televerser_fichier(chemin))
        yield fichiers
    finally:
        for fichier in fichiers:
            supprimer_fichier(fichier)


@contextlib.contextmanager
def documents_pour_gemini(documents):
    """Prepare documents for a prompt. `documents` is a list of (label, path).

    With PDF_VIA_GEMINI on, a PDF is uploaded and Gemini reads the pages itself, formulas and figures
    included. Any other file, or a PDF when the setting is off, is read as text.
    Yields (parts, texts): the labels and files to put in front of the prompt, and the text of each
    document by label (None for a document that was uploaded).
    """
    a_envoyer = [(etiquette, chemin) for etiquette, chemin in documents
                 if settings.PDF_VIA_GEMINI and str(chemin).lower().endswith(".pdf")]
    with fichiers_televerses([chemin for _, chemin in a_envoyer]) as fichiers:
        envoyes = {etiquette: fichier for (etiquette, _), fichier in zip(a_envoyer, fichiers)}
        parties, textes = [], {}
        for etiquette, chemin in documents:
            if etiquette in envoyes:
                parties.extend([f"{etiquette} (PDF joint) :", envoyes[etiquette]])
                textes[etiquette] = None
            else:
                textes[etiquette] = extraire_contenu_fichier(chemin)
        yield parties, textes


def _annoncer_attente(arret, derniere_donnee):
    """Print a message every INTERVALLE_PROGRES seconds while Gemini is silent, so that a wait does not look like a freeze."""
    while not arret.wait(settings.INTERVALLE_PROGRES):
        silence = time.monotonic() - derniere_donnee[0]
        if silence >= settings.INTERVALLE_PROGRES:
            log(f"   ... en attente de Gemini : {round(silence)} s sans donnée (abandon à {settings.DELAI_APPEL_GEMINI} s)")


def _taille_pensees(morceau):
    """Number of characters of thoughts summary in one piece of an answer (0 for a piece of the answer itself)."""
    candidats = getattr(morceau, "candidates", None)
    if not candidats or not candidats[0].content or not candidats[0].content.parts:
        return 0
    return sum(len(p.text) for p in candidats[0].content.parts if p.thought is True and isinstance(p.text, str))


def config_reflexion(model_name):
    """The thinking options to ask of a model, or None when there is none or when the model refused them.

    They come from RESUME_PENSEES, NIVEAU_REFLEXION and BUDGET_REFLEXION.
    """
    if model_name in _modeles_sans_pensees:
        return None
    options = {}
    if settings.RESUME_PENSEES:
        options["include_thoughts"] = True
    if settings.NIVEAU_REFLEXION:
        options["thinking_level"] = settings.NIVEAU_REFLEXION
    if settings.BUDGET_REFLEXION is not None:
        options["thinking_budget"] = settings.BUDGET_REFLEXION
    return types.ThinkingConfig(**options) if options else None


def _est_une_erreur_de_reflexion(erreur):
    """True for the error of a model that does not accept the thoughts summary."""
    return (isinstance(erreur, errors.APIError) and getattr(erreur, "code", None) == 400
            and any(mot in str(erreur).lower() for mot in ("thinking", "thought")))


def _lire_flux(model_name, contents, config):
    """Read one streamed answer. See lire_flux."""
    debut = derniere_annonce = time.monotonic()
    derniere_donnee = [debut]
    arret = threading.Event()
    veilleur = threading.Thread(target=_annoncer_attente, args=(arret, derniere_donnee), daemon=True)
    veilleur.start()
    morceaux, taille, pensees = [], 0, 0
    flux = get_client().models.generate_content_stream(model=model_name, contents=contents, config=config)
    try:
        for morceau in flux:
            if morceau.text:
                morceaux.append(morceau.text)
                taille += len(morceau.text)
            pensees += _taille_pensees(morceau)
            maintenant = time.monotonic()
            derniere_donnee[0] = maintenant
            if maintenant - debut > settings.DUREE_MAX_REPONSE:
                raise ReponseTropLongue(f"le modèle {model_name} écrit encore sa réponse après {settings.DUREE_MAX_REPONSE} s ({taille} caractères reçus)")
            if maintenant - derniere_annonce >= settings.INTERVALLE_PROGRES:
                if taille == 0 and pensees:
                    log(f"   ... le modèle réfléchit : {pensees} caractères de pensées reçus ({round(maintenant - debut)} s)")
                else:
                    log(f"   ... réponse en cours : {taille} caractères reçus ({round(maintenant - debut)} s)")
                derniere_annonce = maintenant
    finally:
        arret.set()
        veilleur.join(timeout=1)
        fermer = getattr(flux, "close", None)
        if fermer:
            fermer()
    return "".join(morceaux)


def lire_flux(model_name, contents, config):
    """Ask Gemini for an answer and read it piece by piece. Returns the whole text.

    The answer arrives as a stream, so DELAI_APPEL_GEMINI only counts the silence between two pieces:
    a long answer is never cut while it keeps coming. A message every INTERVALLE_PROGRES seconds shows that
    it does. While Gemini is silent, another message says how long. An answer that never ends is stopped
    after DUREE_MAX_REPONSE seconds.

    With RESUME_PENSEES on, Gemini is also asked for a summary of its thoughts. Those pieces arrive while the
    model thinks, which keeps the silence away and shows the activity. They are not part of the text that is
    returned. NIVEAU_REFLEXION and BUDGET_REFLEXION, when set, tell the model how much to think. A model that
    refuses these options is asked again without them, and not asked for them again.
    """
    reflexion = config_reflexion(model_name)
    if reflexion is not None:
        try:
            return _lire_flux(model_name, contents, {**config, "thinking_config": reflexion})
        except Exception as e:
            if not _est_une_erreur_de_reflexion(e):
                raise
            _modeles_sans_pensees.add(model_name)
            log(f"Avertissement : le modèle {model_name} n'accepte pas les réglages de réflexion "
                "(RESUME_PENSEES, NIVEAU_REFLEXION, BUDGET_REFLEXION). On continue sans.")
    return _lire_flux(model_name, contents, config)


def formules_hors_dollars(fiche):
    """Extracts of the sheet where LaTeX is written outside $...$ (Notion would show it as plain text).

    The extracts are counted before the repair that puts them between dollars.
    """
    extraits = []
    for texte in fiche.textes_avec_formules():
        extraits.extend(latex_hors_dollars(texte))
    return extraits


def generer_fiche(contents, schema):
    """Ask Gemini for a sheet. Each model is tried TENTATIVES_PAR_MODELE times, then the next model.

    These answers count as a failed try: an answer that does not respect the schema (empty, truncated,
    invalid JSON), and a sheet with no content. A short pause separates two tries of the same model,
    because an overloaded server rarely recovers within a second. Every kind of error is retried alike,
    except a model that goes silent for DELAI_APPEL_GEMINI seconds, or whose answer lasts longer than
    DUREE_MAX_REPONSE seconds: that model is stuck or slow, so the next model is tried at once.

    A sheet with LaTeX written outside $...$ is kept: the cleaning puts that LaTeX between dollars when the page
    is built. The warning says how many extracts were found, so that you know the model did not follow the rule.
    """
    derniere_exception = None
    config = {
        "response_mime_type": "application/json",
        "response_schema": schema,
        "temperature": 0.0
    }
    essais = settings.TENTATIVES_PAR_MODELE
    for model_name in settings.GEMINI_MODELS:
        for essai in range(1, essais + 1):
            try:
                if essai == 1:
                    log(f"Tentative de génération avec le modèle : {model_name}...")
                else:
                    log(f"Tentative de génération avec le modèle : {model_name} (essai {essai}/{essais})...")
                fiche = schema.model_validate_json(lire_flux(model_name, contents, config))
                if fiche.est_vide():
                    raise ValueError("fiche vide : la réponse ne contient aucun contenu")
            except Exception as e:
                derniere_exception = e
                if est_un_delai_depasse(e):
                    suite = " Essai du modèle suivant..." if model_name != settings.GEMINI_MODELS[-1] else ""
                    if isinstance(e, ReponseTropLongue):
                        derniere_exception = e
                        log(f"Avertissement : {e}.{suite}")
                    else:
                        derniere_exception = TimeoutError(f"pas de réponse du modèle {model_name} en {settings.DELAI_APPEL_GEMINI} s")
                        log(f"Avertissement : le modèle {model_name} n'a pas répondu en {settings.DELAI_APPEL_GEMINI} s.{suite}")
                    break
                if essai < essais:
                    log(f"Avertissement : Échec avec le modèle {model_name} ({e}). Nouvel essai dans {settings.PAUSE_ENTRE_ESSAIS} s...")
                    time.sleep(settings.PAUSE_ENTRE_ESSAIS)
                else:
                    log(f"Avertissement : Échec avec le modèle {model_name} ({e}). Essai du modèle suivant...")
                continue
            extraits = formules_hors_dollars(fiche)
            if extraits:
                log(f"Avertissement : le modèle {model_name} a écrit {len(extraits)} extrait(s) de LaTeX hors de $...$ "
                    f"(par exemple : {extraits[0]}). Ils sont mis entre $ automatiquement : vérifie les formules dans la page.")
            return fiche
    raise RuntimeError(f"Tous les modèles Gemini de fallback ont échoué. Dernière erreur : {derniere_exception}")
