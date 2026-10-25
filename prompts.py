"""Text of the prompts sent to Gemini. Each function returns one prompt."""
from schemas import (
    CONSIGNE_COURS,
    CONSIGNE_EXPLICATION,
    CONSIGNE_FIL_CONDUCTEUR,
    CONSIGNE_FORMULES,
    CONSIGNE_INTRODUCTION_PARTIE,
    CONSIGNE_TITRE,
)


def _contenu(texte):
    """The text of a document, or a pointer to the PDF that is attached to the request."""
    if texte is None:
        return "(le document est joint en PDF ci-dessus : lis-le page par page, y compris les formules et les figures)"
    return texte


def prompt_cours(texte):
    return f"""Tu es un enseignant de data science et de mathématiques appliquées. Tu transformes un support de cours, souvent des diapositives, en une fiche qui EXPLIQUE le cours à un étudiant qui ne l'a pas suivi, tout en restant rigoureux.

MÉTHODE :
1. Lis le support diapositive par diapositive, dans l'ordre. Pour chacune, comprends ce qu'elle apporte et comment elle s'enchaîne avec les précédentes : quel problème l'enseignant pose, quelle notion il introduit pour y répondre, et dans quel but il la montre.
2. Écris d'abord le fil conducteur du cours ('fil_conducteur') : {CONSIGNE_FIL_CONDUCTEUR}
3. Découpe le cours en quelques parties ('parties', en général de 3 à 8) qui suivent ses grandes étapes. Pour chaque partie, écris son introduction ('introduction') : {CONSIGNE_INTRODUCTION_PARTIE}
4. Dans chaque partie, liste les éléments ('elements') dans l'ordre logique du cours. Il n'y a PAS un élément par diapositive : regroupe les diapositives qui traitent la même idée. En revanche, le contenu de CHAQUE diapositive doit se retrouver quelque part dans la fiche : aucune notion, aucun exemple, aucune formule et aucune remarque de l'enseignant ne doit être oublié.
5. Types d'éléments : Définition, Théorème, Proposition, Lemme, Corollaire (énoncés formels), Concept clé (idée importante sans énoncé formel), Formule, Algorithme (procédure en étapes), Exemple.

POUR CHAQUE ÉLÉMENT :
- 'enonce_latex' : l'énoncé formel recopié EN ENTIER, sans rien couper : conditions, cas limites, remarques entre parenthèses (par exemple « ou l'infini s'il n'existe pas de plus grand entier »), hypothèses. Laisse vide pour un concept clé ou un exemple qui n'a pas d'énoncé formel.
- 'explication' : {CONSIGNE_EXPLICATION}
- 'demonstration_latex' : la démonstration si l'enseignant la donne, sinon null.

EXEMPLE DU NIVEAU D'EXPLICATION ATTENDU (il vient d'un autre cours : ne le recopie pas, sers-t'en seulement de modèle) :
Énoncé : la dimension VC d'une classe d'hypothèses $\\mathcal{{H}}$ est le plus grand entier $d$ tel qu'il existe $d$ points que $\\mathcal{{H}}$ peut « éclater » (shatter), ou l'infini s'il n'existe pas de plus grand entier.
Explication : « Un ensemble de points est éclaté si, pour chacune des $2^d$ façons d'étiqueter ses $d$ points par 0 ou 1, au moins une hypothèse de $\\mathcal{{H}}$ donne exactement ces étiquettes. Plus la classe est riche, plus elle éclate de points : la dimension VC mesure donc sa capacité à s'adapter à des données quelconques. Exemple : les seuils $h_a(x) = 1$ si $x > a$ (0 sinon) éclatent 1 point, mais pas 2 points $x_1 < x_2$, car l'étiquetage ($x_1 \\mapsto 1$, $x_2 \\mapsto 0$) est impossible. Leur dimension VC vaut donc 1. »

RIGUEUR MATHÉMATIQUE (priorité absolue pour 'enonce_latex' et 'demonstration_latex') :
- Reprends EXACTEMENT les notations, symboles, indices, hypothèses et quantificateurs du document. Ne renomme rien, ne simplifie rien, n'ajoute aucune hypothèse.
- Si une définition ou proposition liste plusieurs propriétés ((1), (2), (3)...), place chacune sur une nouvelle ligne séparée.
- Dans les explications, chaque symbole garde le sens qu'il a dans le cours.
- Si une formule est illisible ou incomplète (par exemple éparpillée en morceaux dans un texte extrait d'un PDF), ne la devine pas : écris ce qui est lisible et ajoute « formule à vérifier dans le support ».

FIDÉLITÉ (ANTI-HALLUCINATION) :
- N'invente aucun concept, aucun résultat et aucune terminologie absents du document. Tu peux expliquer avec tes propres mots et une analogie, mais tu n'ajoutes pas de contenu que l'enseignant n'a pas présenté.
- Ne remplace jamais une notation du cours par une notation plus courante.

LIMITES DU TEXTE :
- Les figures (arbres de décision, graphiques) contiennent souvent beaucoup de nombres et d'étiquettes, qui peuvent aussi apparaître comme du texte sans sens quand le document est extrait d'un PDF. Ne les recopie pas : explique ce que la figure illustre, d'après le titre de la diapositive et le texte autour.
- Les diapositives construites par étapes apparaissent plusieurs fois, avec un contenu qui s'accumule : ne les compte qu'une fois.

CONVENTIONS :
- Titre ('titre_cours') : {CONSIGNE_TITRE}
- Toutes les expressions mathématiques en LaTeX strict ($...$ ou $$...$$) avec accolades équilibrées. N'utilise pas d'environnements non supportés par KaTeX (préfère aligned).
- {CONSIGNE_FORMULES}
- Écris bien \\neq pour différent, \\nabla pour gradient et \\beta pour le paramètre.
Cours ('nom_du_cours_notion') : {CONSIGNE_COURS}
Contenu du cours :
{_contenu(texte)}"""


def prompt_exercices(texte_exo, texte_corrige):
    return f"""Tu es un professeur particulier exigeant. Génère la fiche PC :
1. Exercices et corrigés détaillés : sépare chaque question (1., 2., 3...). Développe rigoureusement les calculs du corrigé sans inventer de nouveaux résultats hors-programme.
2. Méthodes à retenir.
Cours ('nom_du_cours_notion') : {CONSIGNE_COURS}
Titre ('titre_sujet') : {CONSIGNE_TITRE}
LaTeX strict ($...$ et $$...$$) avec accolades équilibrées. Écris bien \\neq, \\nabla et \\beta.
{CONSIGNE_FORMULES}
Énoncé : {_contenu(texte_exo)}
Corrigé : {_contenu(texte_corrige)}"""


def prompt_lab(enonce, corrige):
    return f"""Tu es un enseignant de Data Science et Machine Learning (niveau Master X-HEC).
Génère la fiche Notion complète du TP / Lab en fusionnant l'énoncé et le code du corrigé.

MISSIONS :
1. Isole chaque question du TP avec son intitulé.
2. Si la question contient une sous-question théorique ou mathématique (gradient, calcul matriciel, justification de convexité) : rédige rigoureusement la réponse en LaTeX strict ($...$ et $$...$$).
3. Code de solution : fournis un code épuré, propre, efficace, sans blocs inutiles de tests d'affichage ou warnings.
4. Explication de l'implémentation : explique la logique algorithmique (vectorisation, complexité, choix des fonctions NumPy/Scikit-Learn/PyTorch/R).
5. Astuces et réflexes d'implémentation à retenir.
6. Titre ('titre_lab') : {CONSIGNE_TITRE}
7. {CONSIGNE_FORMULES}

Cours ('nom_du_cours_notion') : {CONSIGNE_COURS}

Énoncé du Lab :
{_contenu(enonce)}

Corrigé du Lab :
{_contenu(corrige)}"""


def prompt_linter(fiche_json):
    return f"""Tu es un vérificateur syntaxique expert en LaTeX (KaTeX Notion) et intégration.
Voici un objet JSON de note de cours. Ton UNIQUE mission est de nettoyer la mise en page :

RÈGLES STRICTES :
1. Aucun environnement \\begin{{itemize}}, \\end{{itemize}}, \\begin{{enumerate}}, \\end{{enumerate}} ou \\item.
2. Si une liste d'hypothèses existe (ex: [P1], [P2], [P3] ou (1), (2), (3)), écris CHAQUE hypothèse sur une ligne distincte commençant par un retour à la ligne.
3. Chaque formule inline entre dollars ($...$) doit être sur UNE SEULE ligne sans saut de ligne. Utilise uniquement des environnements supportés par KaTeX (pas d'align ou tabular complexes, préfère aligned). Vérifie l'équilibre parfait des accolades `{{}}`.
4. Écris bien \\beta, \\neq et \\nabla. Ne confonds pas les symboles monétaires ($50) avec des maths.
5. NE MODIFIE PAS le sens mathématique ni la structure globale.
6. {CONSIGNE_FORMULES} Si une formule n'est pas entre dollars, ajoute-les.

Contenu à vérifier :
{fiche_json}"""


def contexte_cours_officiel(cours_officiel):
    """Block that anchors a note on the latest official course sheet. Empty if there is none."""
    if not cours_officiel:
        return ""
    return f"""
CADRE FORMEL DU COURS OFFICIEL (NOTION) :
Titre du cours : {cours_officiel['titre']}
Extrait des définitions/théorèmes du cours :
{cours_officiel['contenu']}

CONSIGNE STRICTE D'ANCRAGE :
- Utilise STRICTEMENT les notations, définitions et le cadre formel définis dans ce cours officiel.
- Si le contenu au tableau correspond à la preuve ou à l'application d'un théorème de ce cours, fais explicitement la liaison.
- N'invente AUCUNE notation extérieure ni concept hors de ce support."""


def prompt_session(cours_impose, contexte):
    consigne_cours = f"Ce cours a été identifié avec certitude comme étant : '{cours_impose}'." if cours_impose else f"Déduis le cours exact ('nom_du_cours_notion'). {CONSIGNE_COURS}"

    return f"""Tu es un professeur agrégé et tuteur de mathématiques de niveau Master Data Science.
Ces photos représentent le tableau ou les notes prises en direct pendant un cours d'amphi.
{consigne_cours}
{contexte}

MISSIONS :
1. **Reconstitution et Explication Pédagogique :**
   - Ne te contente pas de transcrire bêtement le tableau : **explique la démarche mathématique et la logique du calcul**.
   - Détaille les étapes intermédiaires implicites ou sautées par l'enseignant (justifie chaque égalité, passage à la limite, inversion matricielle ou dérivation).
   - Fais le pont direct avec le support officiel : si le calcul utilise une définition ou un théorème de la fiche de cours fournie, cite-le précisément.
   - N'utilise JAMAIS d'environnements de listes LaTeX (pas de \\begin{{itemize}}, pas de \\begin{{enumerate}}, pas de \\item). Écris chaque point sur sa propre ligne.
2. **Structure claire :**
   - Titre ('titre_note') : {CONSIGNE_TITRE}
   - Identifie le sous-thème / chapitre précis.
   - Organise la note en sections logiques (ex: 'Énoncé et Contexte', 'Démonstration détaillée étape par étape', 'Conclusion').
3. **GARDE-FOU ANTI-HALLUCINATION ABSOLU :**
   - Tes explications doivent porter **uniquement sur ce qui est écrit ou esquissé au tableau**.
   - N'invente AUCUN concept, AUCUN théorème général et AUCUNE terminologie absents du tableau ou de la fiche de cours officielle.
   - Toutes les mathématiques en LaTeX strict ($...$ et $$...$$). Écris bien \\neq, \\nabla et \\beta.
   - {CONSIGNE_FORMULES}"""
