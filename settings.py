"""Settings of the course assistant.

Secrets and machine specific paths are read from the environment (a .env file when
you work locally). This is the only module that reads the environment. Everything
else (timetable, folder names, limits) is plain constants that you can edit here.
"""
import os
import types

from dotenv import load_dotenv

load_dotenv()

# --- Secrets and paths: put them in .env, never in the code ---
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DRIVE_ROOT = os.path.expanduser(os.getenv("DRIVE_ROOT", ""))

# Models tried in this order until one answers
GEMINI_MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.8-flash").split(",")
    if m.strip()
]

NIVEAUX_REFLEXION = ("minimal", "low", "medium", "high")


def _entier_optionnel(nom):
    """An integer read from the environment, or None when the variable is empty."""
    brut = os.getenv(nom)
    if brut is None or not brut.strip():
        return None
    try:
        return int(brut)
    except ValueError:
        raise ValueError(f"{nom} doit être un nombre entier (valeur reçue : {brut!r}).") from None


def _niveau_optionnel(nom):
    """A thinking level read from the environment, or None when the variable is empty."""
    brut = (os.getenv(nom) or "").strip().lower()
    if not brut:
        return None
    if brut not in NIVEAUX_REFLEXION:
        raise ValueError(f"{nom} doit valoir l'un de : {', '.join(NIVEAUX_REFLEXION)} (valeur reçue : {brut!r}).")
    return brut


# How much a model thinks before it writes. Leave both empty to keep the default of each model.
# Use one of them: NIVEAU_REFLEXION for the models that work with levels, BUDGET_REFLEXION for the models
# that work with a number of tokens (-1: the model decides, 0: no thinking).
NIVEAU_REFLEXION = _niveau_optionnel("NIVEAU_REFLEXION")
BUDGET_REFLEXION = _entier_optionnel("BUDGET_REFLEXION")

# --- Constants ---
TENTATIVES_PAR_MODELE = 2   # how many times a Gemini model is tried before the next one
PAUSE_ENTRE_ESSAIS = 3      # seconds to wait before trying the same model again
DELAI_APPEL_GEMINI = 400    # seconds without any data from Gemini before a request is given up (a stalled answer, an upload)
DUREE_MAX_REPONSE = 900     # seconds after which an answer that keeps coming is given up too (a model that loops)
RESUME_PENSEES = True       # ask Gemini for a summary of its thoughts: it arrives while the model thinks and shows that it works
INTERVALLE_PROGRES = 10     # seconds between two messages while an answer is being received, or while Gemini is silent
PDF_VIA_GEMINI = True       # True: a PDF is sent to Gemini as a file. False: its text is extracted with pymupdf4llm
PDF_AVEC_OCR = False        # only when PDF_VIA_GEMINI is False: True lets pymupdf4llm read pages without text (scans) with OCR
DELAI_TRAITEMENT_FICHIER = 120     # seconds Gemini may take to get an uploaded file ready before it is given up
INTERVALLE_SONDAGE_FICHIER = 2     # seconds between two checks of an uploaded file
NOTION_VERSION = "2022-06-28"
EXT_LABS = {".ipynb", ".rmd", ".html", ".pdf"}
LIMITE_LAB_CARS = 120_000   # maximum characters read from a lab statement or correction
LIMITE_EQUATION = 1000      # Notion limit for one equation
TAILLE_TOGGLE = 90          # blocks per toggle before a "(suite n)" toggle is started
LIMITE_TITRE = 80           # maximum characters of a page title
NODE_COMMAND = "node"        # command that starts Node.js, used to check formulas with KaTeX
SEUIL_FIDELITE_CODE = 0.7   # share of the code lines of a lab correction that must be found in the code Gemini writes
DELAI_KATEX = 60            # seconds allowed for one formula check

# The courses of the Notion database, each with a short description. Gemini compares the content
# of a document with these descriptions to choose the course. The names must be the values of the
# "Cours" column in Notion. The timetable below must use these names.
COURS = {
    "Statistics & Probability in R": (
        "Estimation, hypothesis testing, confidence intervals, the Bayesian approach, "
        "chi-squared tests and non parametric tests, with R."
    ),
    "Machine Learning": (
        "Introduction to machine learning: regression and classification, linear models, nearest "
        "neighbors and tree-based models. How to train, evaluate and use models and data correctly."
    ),
    "Linear Algebra & Regression": (
        "Theory of statistical linear models: exploratory methods, simple linear regression, "
        "inference and diagnostics, matrix and geometric formulation, multiple linear regression, "
        "ANOVA, ANCOVA, model selection and regularization."
    ),
    "Probability Refresher": (
        "Pure probability, without application. Choose it only if the document has no application "
        "(no data, estimation, test or model). Otherwise choose the main course."
    ),
    "Linear Algebra Refresher": (
        "Pure linear algebra, without application. Choose it only if the document has no application "
        "(no data, regression or model). Otherwise choose the main course."
    ),
}

# Weekly timetable. 0 = Monday ... 4 = Friday. A photo taken in a slot gets that course.
PLANNING = [
    # Monday
    {"jour": 0, "debut": (9, 30), "fin": (12, 30), "cours": "Machine Learning", "categorie": "Notes de cours"},
    {"jour": 0, "debut": (14, 0), "fin": (17, 0), "cours": "Machine Learning", "categorie": "PC"},
    # Wednesday
    {"jour": 2, "debut": (9, 30), "fin": (12, 30), "cours": "Linear Algebra & Regression", "categorie": "Notes de cours"},
    {"jour": 2, "debut": (14, 0), "fin": (17, 0), "cours": "Linear Algebra & Regression", "categorie": "PC"},
    # Friday
    {"jour": 4, "debut": (9, 30), "fin": (12, 30), "cours": "Statistics & Probability in R", "categorie": "Notes de cours"},
    {"jour": 4, "debut": (14, 0), "fin": (17, 0), "cours": "Statistics & Probability in R", "categorie": "PC"},
]


def dossiers():
    """Folders of the Drive tree, built from DRIVE_ROOT at the time of the call."""
    return types.SimpleNamespace(
        cours=os.path.join(DRIVE_ROOT, "01_A_Traiter", "Cours"),
        exos=os.path.join(DRIVE_ROOT, "01_A_Traiter", "Exercices"),
        corriges=os.path.join(DRIVE_ROOT, "01_A_Traiter", "Corriges"),
        labs=os.path.join(DRIVE_ROOT, "01_A_Traiter", "Labs"),
        labs_corriges=os.path.join(DRIVE_ROOT, "01_A_Traiter", "Labs_Corriges"),
        notes=os.path.join(DRIVE_ROOT, "01_A_Traiter", "Notes_Manuscrites"),
        archives=os.path.join(DRIVE_ROOT, "02_Archives"),
    )


def reglages_manquants():
    """Names of the required settings that are empty."""
    valeurs = {
        "NOTION_TOKEN": NOTION_TOKEN,
        "NOTION_DATABASE_ID": NOTION_DATABASE_ID,
        "GEMINI_API_KEY": GEMINI_API_KEY,
        "DRIVE_ROOT": DRIVE_ROOT,
    }
    return [nom for nom, valeur in valeurs.items() if not valeur]


def masquer(texte):
    """Replace every secret found in a text by ***."""
    for secret in (NOTION_TOKEN, GEMINI_API_KEY, NOTION_DATABASE_ID):
        if secret:
            texte = texte.replace(secret, "***")
    return texte


def log(message=""):
    """Print a message with the secrets hidden. All output of the program goes through here."""
    print(masquer(str(message)))
