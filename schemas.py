"""Shapes of the sheets that Gemini must return (Pydantic models)."""
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from settings import COURS, LIMITE_TITRE

# The courses are defined once, with their descriptions, in settings.COURS.
CoursAutorises = Literal[tuple(COURS)]

# One rule for the choice of the course, given to Gemini in the schema and in the prompts.
CONSIGNE_COURS = "Choisis le cours d'après le contenu du document, parmi :\n" + "\n".join(
    f'- "{nom}" : {description}' for nom, description in COURS.items()
)

# One rule for the formulas, given to Gemini in the schema and in the prompts. A formula that is not
# between dollar signs is sent to Notion as plain text, so it is checked in gemini_client.py.
CONSIGNE_FORMULES = (
    "Toute formule mathématique, même une simple lettre avec indice comme $P_X$ ou $h_{ij}$, doit être écrite "
    "entre $...$ (ou entre $$...$$ si elle est seule sur sa ligne). Même un énoncé qui est entièrement une formule "
    "s'écrit entre $...$ : le texte ne contient jamais de LaTeX nu. "
    "N'écris jamais une commande LaTeX (\\leq, \\frac, \\text, \\dots, \\top...) en dehors des $."
)

# The explanation rules of a course sheet, given to Gemini in the schema and in the prompt.
CONSIGNE_EXPLICATION = (
    "Explication de l'énoncé, en français, pour un étudiant qui découvre la notion. Dis ce que chaque symbole "
    "représente, ce que l'énoncé dit vraiment avec des mots simples (pas une simple reformulation), pourquoi il est "
    "vrai ou raisonnable, et donne un exemple concret tiré du cours. Pour un concept clé ou un exemple sans énoncé "
    "formel, explique l'idée. Garde en anglais les termes techniques du cours et explique-les à leur première "
    "apparition. " + CONSIGNE_FORMULES
)
CONSIGNE_INTRODUCTION_PARTIE = (
    "Introduction de la partie, en français, en quelques phrases claires (pas une liste) : le problème dont elle "
    "s'occupe, pourquoi l'enseignant l'aborde à ce moment (le lien avec la partie précédente), ce que la partie va "
    "apporter et où elle mène. Si le document ne le dit pas, déduis-le de l'enchaînement des diapositives et "
    "formule-le avec prudence (par exemple « probablement pour... »). " + CONSIGNE_FORMULES
)
CONSIGNE_FIL_CONDUCTEUR = (
    "Vue d'ensemble du cours en quelques phrases, en français : de quoi il parle et comment ses parties "
    "s'enchaînent. " + CONSIGNE_FORMULES
)

# One rule for the title of every page (course, exercises, lab, notes). The course name and the
# type of page are already in Notion columns, so the title holds the subject only.
CONSIGNE_TITRE = (
    f"Titre de la page : seulement le sujet traité, en texte brut (pas de LaTeX), {LIMITE_TITRE} caractères maximum. "
    "Si le document commence par une page ou une slide de titre, pars de celle-ci. "
    "Retire le numéro de catalogue du cours, le nom de l'enseignant, le nom de la matière, "
    "le type de document (cours, PC, TD, lab, notes), la date et toute numérotation. "
    "Pour des notes de tableau, déduis le sujet du contenu mathématique."
)


# --- Course ---
class ElementMathematique(BaseModel):
    type_element: Literal[
        "Définition", "Théorème", "Proposition", "Lemme", "Corollaire",
        "Concept clé", "Formule", "Algorithme", "Exemple",
    ]
    nom: str
    enonce_latex: str = Field(description=(
        "Énoncé formel exact, avec les notations du cours, recopié EN ENTIER : conditions, cas limites, remarques "
        "entre parenthèses, hypothèses. Chaque condition (1), (2), (3) doit être sur une ligne séparée. Laisser vide "
        "pour un concept clé ou un exemple qui n'a pas d'énoncé formel. " + CONSIGNE_FORMULES
    ))
    explication: str = Field(description=CONSIGNE_EXPLICATION)
    demonstration_utile: bool
    demonstration_latex: Optional[str] = Field(description=CONSIGNE_FORMULES)


class PartieCours(BaseModel):
    titre_partie: str = Field(description="Titre court de la partie : le sujet qu'elle traite, sans numéro.")
    introduction: str = Field(description=CONSIGNE_INTRODUCTION_PARTIE)
    elements: List[ElementMathematique]


class FicheCours(BaseModel):
    type_fiche: Literal["Cours"] = "Cours"
    titre_cours: str = Field(description=CONSIGNE_TITRE)
    nom_du_cours_notion: CoursAutorises = Field(description=CONSIGNE_COURS)
    fil_conducteur: str = Field(description=CONSIGNE_FIL_CONDUCTEUR)
    parties: List[PartieCours] = Field(description="Quelques parties (en général de 3 à 8) qui suivent les grandes étapes du cours.")

    def est_vide(self):
        return not any(partie.elements for partie in self.parties)

    def textes_avec_formules(self):
        textes = [self.fil_conducteur]
        for partie in self.parties:
            textes.append(partie.introduction)
            for element in partie.elements:
                textes.extend([element.enonce_latex, element.explication])
                if element.demonstration_latex:
                    textes.append(element.demonstration_latex)
        return textes


# --- Exercises ---
class ExerciceCorrige(BaseModel):
    numero_exo: str
    enonce_latex: str = Field(description=CONSIGNE_FORMULES)
    correction_etofee_latex: str = Field(description=CONSIGNE_FORMULES)


class FicheExercices(BaseModel):
    type_fiche: Literal["Exercices"] = "Exercices"
    titre_sujet: str = Field(description=CONSIGNE_TITRE)
    nom_du_cours_notion: CoursAutorises = Field(description=CONSIGNE_COURS)
    exercices: List[ExerciceCorrige]
    methode_a_retenir: List[str]

    def est_vide(self):
        return not self.exercices

    def textes_avec_formules(self):
        textes = []
        for exercice in self.exercices:
            textes.extend([exercice.enonce_latex, exercice.correction_etofee_latex])
        return textes + list(self.methode_a_retenir)


# --- Labs (Python and R) ---
class PartieLab(BaseModel):
    titre_question: str = Field(description="Numéro et titre de la question du TP (ex: 'Question 1 : Régression Ridge vectorisée').")
    enonce_ou_contexte: str = Field(description="Énoncé court et objectif algorithmique ou mathématique.")
    justification_theorique_latex: Optional[str] = Field(default=None, description="Si la question comporte des maths (dérivation, convexité, justification de formule), développe ici en LaTeX strict. Sinon, laisser vide. " + CONSIGNE_FORMULES)
    langage_code: Literal["python", "r"] = Field(description="Langage employé : 'python' ou 'r'.")
    code_solution_propre: str = Field(description="Code de la solution propre, sans scories, sans imports redondants, bien indenté.")
    explication_code: str = Field(description="Explication pédagogique de l'implémentation (complexité, choix des fonctions, vectorisation).")

    @field_validator('langage_code', mode='before')
    @classmethod
    def normaliser_langage(cls, v: Any) -> str:
        if isinstance(v, str):
            return v.lower().strip()
        return v


class FicheLab(BaseModel):
    type_fiche: Literal["Lab"] = "Lab"
    titre_lab: str = Field(description=CONSIGNE_TITRE)
    nom_du_cours_notion: CoursAutorises = Field(description=CONSIGNE_COURS)
    langage_principal: Literal["python", "r"]
    parties: List[PartieLab]
    reflexes_et_astuces_code: List[str] = Field(description="Bonnes pratiques d'implémentation et astuces clés à retenir.")

    @field_validator('langage_principal', mode='before')
    @classmethod
    def normaliser_langage(cls, v: Any) -> str:
        if isinstance(v, str):
            return v.lower().strip()
        return v

    def est_vide(self):
        return not self.parties

    def textes_avec_formules(self):
        """The prose of the lab. The code of the solution is left out: it is not LaTeX."""
        textes = []
        for partie in self.parties:
            textes.extend([partie.enonce_ou_contexte, partie.explication_code])
            if partie.justification_theorique_latex:
                textes.append(partie.justification_theorique_latex)
        return textes + list(self.reflexes_et_astuces_code)


# --- Handwritten notes and board photos ---
class SectionNote(BaseModel):
    titre_section: str = Field(description="Titre de la partie ou du résultat.")
    contenu_latex: str = Field(description="Contenu rédigé au propre, rigoureux, en LaTeX strict ($...$ et $$...$$). " + CONSIGNE_FORMULES)


class FicheNotesManuscrites(BaseModel):
    type_fiche: Literal["Notes"] = "Notes"
    titre_note: str = Field(description=CONSIGNE_TITRE)
    nom_du_cours_notion: CoursAutorises = Field(description=CONSIGNE_COURS)
    chapitre_associe: str = Field(description="Le sous-thème ou chapitre précis.")
    sections: List[SectionNote] = Field(description="Sections de la note.")
    remarques_importantes: List[str] = Field(description="Points de vigilance, astuces de calcul ou remarques orales du prof notées au tableau.")

    def est_vide(self):
        return not self.sections

    def textes_avec_formules(self):
        return [section.contenu_latex for section in self.sections] + list(self.remarques_importantes)
