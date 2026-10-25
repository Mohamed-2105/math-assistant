"""Fakes and a small invented sample used by the tests.

No key and no network are needed. Nothing here comes from real course material.
The sample holds a few awkward cases: a formula that starts with a digit, a price, a LaTeX
command damaged by a JSON newline, an equation longer than the Notion limit and an R lab.
"""
import contextlib
import io
import json
import os
import re
import tempfile
import threading
import types
from pathlib import Path
from unittest import mock

import httpx
from google.genai import errors

import gemini_client
import katex_validator
import notion_api
import pipeline
import settings
import sources

FAKE_NOTION_TOKEN = "test-notion-token-0000"
FAKE_GEMINI_KEY = "test-gemini-key-0000"
FAKE_DATABASE_ID = "00000000000000000000000000000000"
FAKE_MODELS = ["fake-model-a", "fake-model-b"]


# ---------------------------------------------------------------- fake Notion

class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


class FakeNotion:
    """Stands in for the httpx module. Records every request."""

    def __init__(self, reject_first_post=False, reject_all_posts=False, reject_first_patch=False):
        self.reject_first_post = reject_first_post
        self.reject_all_posts = reject_all_posts
        self.reject_first_patch = reject_first_patch
        self.requests = []
        self.page_bodies = []      # every body sent to POST /v1/pages, accepted or not
        self.patch_bodies = []
        self.pages_created = 0
        self._posts = 0
        self._patches = 0

    def _handle(self, method, url, headers, body, timeout):
        path = url.replace("https://api.notion.com", "")
        headers = {k: v.replace(FAKE_NOTION_TOKEN, "<TOKEN>") for k, v in (headers or {}).items()}
        copy = json.loads(json.dumps(body)) if body is not None else None
        self.requests.append({"method": method, "path": path, "headers": headers, "timeout": timeout, "json": copy})
        return self._route(method, path, copy)

    def _route(self, method, path, body):
        if method == "POST" and re.fullmatch(r"/v1/databases/[^/]+/query", path):
            wanted = body["filter"]["and"][0]["select"]["equals"]
            if wanted != "Machine Learning":
                return FakeResponse(200, {"results": []})
            return FakeResponse(200, {"results": [{
                "id": "course-page-1",
                "url": "https://notion.example/course-page-1",
                "properties": {"Name": {"type": "title", "title": [{"plain_text": "Chapter 1: Ridge regression"}]}},
            }]})
        if method == "GET" and re.fullmatch(r"/v1/blocks/[^/?]+/children\?page_size=100", path):
            return FakeResponse(200, {"results": [
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Definition 1. Ridge estimator."}]}},
                {"type": "divider", "divider": {}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Theorem 2. Bias."}]}},
            ]})
        if method == "GET" and re.fullmatch(r"/v1/databases/[^/]+", path):
            return FakeResponse(200, {"properties": {
                "Name": {"type": "title"},
                "Cours": {"type": "select"},
                "Catégorie": {"type": "select"},
            }})
        if method == "POST" and path == "/v1/pages":
            self._posts += 1
            self.page_bodies.append(body)
            if self.reject_all_posts or (self.reject_first_post and self._posts == 1):
                return FakeResponse(400, {"object": "error", "status": 400, "code": "validation_error", "message": "fake error"})
            self.pages_created += 1
            page_id = f"new-page-{self.pages_created}"
            return FakeResponse(200, {"id": page_id, "url": f"https://notion.example/{page_id}"})
        if method == "PATCH" and re.fullmatch(r"/v1/blocks/[^/]+/children", path):
            self._patches += 1
            self.patch_bodies.append(body)
            if self.reject_first_patch and self._patches == 1:
                return FakeResponse(400, {"object": "error", "message": "fake patch error"})
            return FakeResponse(200, {"results": []})
        return FakeResponse(404, {"object": "error", "message": f"unexpected request {method} {path}"})

    def get(self, url, headers=None, timeout=None):
        return self._handle("GET", url, headers, None, timeout)

    def post(self, url, headers=None, json=None, timeout=None):
        return self._handle("POST", url, headers, json, timeout)

    def patch(self, url, headers=None, json=None, timeout=None):
        return self._handle("PATCH", url, headers, json, timeout)


# ---------------------------------------------------------------- fake Gemini


class FakeFichier(str):
    """An uploaded file. It is a string, so that the recorded requests stay readable, with the name and the state
    that the real file object has."""

    def __new__(cls, texte, name, state=None):
        objet = super().__new__(cls, texte)
        objet.name = name
        objet.state = state
        return objet


def etat(nom):
    return types.SimpleNamespace(name=nom)


class FakeMorceau:
    """One piece of a streamed answer: a piece of the answer itself, or of the summary of the thoughts."""

    def __init__(self, text=None, pensees=None):
        self.text = text  # like the real object, the summary of the thoughts is not part of the text
        parts = []
        if pensees:
            parts.append(types.SimpleNamespace(text=pensees, thought=True))
        if text:
            parts.append(types.SimpleNamespace(text=text, thought=False))
        self.candidates = [types.SimpleNamespace(content=types.SimpleNamespace(parts=parts))] if parts else None


class FakeGeminiResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = parsed.model_dump_json()


class FakeGemini:
    """Stands in for the Gemini client (models.generate_content and files.upload)."""

    def __init__(self, fail_first_calls=0, failing_models=(), answers=None, timing_out_models=(), upload_times_out=False,
                 processing_polls=0, failed_files=False, delete_fails=False, piece_size=400, stall_after=None, endless=False, first_piece_delay=0,
                 thinking_pieces=0, thinking_error_models=()):
        self.calls = []
        self.uploads = []
        self.fail_first_calls = fail_first_calls   # the first n calls raise an error
        self.failing_models = set(failing_models)  # every call to these models raises an error
        self.answers = answers  # optional function (model, schema name, contents) -> answer dict
        self.timing_out_models = set(timing_out_models)  # calls to these models get no answer in time
        self.upload_times_out = upload_times_out         # uploads get no answer in time
        self.processing_polls = processing_polls         # an uploaded file stays PROCESSING for this many checks
        self.failed_files = failed_files                 # an uploaded file ends in the FAILED state
        self.delete_fails = delete_fails                 # deleting an uploaded file raises an error
        self.piece_size = piece_size   # characters per piece of a streamed answer
        self.stall_after = stall_after # the stream goes silent (a read timeout) after this many pieces
        self.endless = endless         # the stream never ends
        self.first_piece_delay = first_piece_delay  # real seconds of silence before the first piece
        self.thinking_pieces = thinking_pieces      # pieces of thoughts summary sent before the answer, when asked for
        self.thinking_error_models = set(thinking_error_models)  # these models refuse the thoughts summary (error 400)
        self.polls = 0
        self.deleted = []
        self.models = self
        self.files = self

    def generate_content(self, model, contents, config):
        schema = config.get("response_schema")  # None for a free question, as in the diagnostic
        self.calls.append({
            "model": model,
            "schema": schema.__name__ if schema else None,
            "temperature": config.get("temperature"),
            "thinking": config.get("thinking_config"),
            "contents": list(contents) if isinstance(contents, list) else [contents],
        })
        if model in self.thinking_error_models and config.get("thinking_config") is not None:
            raise errors.ClientError(400, {"error": {"code": 400, "message": "Thinking is not supported by this model.", "status": "INVALID_ARGUMENT"}})
        if model in self.timing_out_models:
            raise httpx.ReadTimeout("timed out")
        if len(self.calls) <= self.fail_first_calls or model in self.failing_models:
            raise RuntimeError("503 UNAVAILABLE (fake)")
        if schema is None:
            return types.SimpleNamespace(text="There are ten prime numbers between 100 and 150.", parsed=None)
        if self.answers:
            reponse = self.answers(model, schema.__name__, self.calls[-1]["contents"])
        else:
            reponse = ANSWERS[schema.__name__]()
        return FakeGeminiResponse(schema.model_validate(reponse))

    def list(self, config=None):
        """The models of the key: some write text, some do not (embeddings, images)."""
        def modele(nom, affichage, actions, entree=1048576, sortie=65536, reflechit=True):
            return types.SimpleNamespace(name=nom, display_name=affichage, supported_actions=actions,
                                         input_token_limit=entree, output_token_limit=sortie, thinking=reflechit)
        return iter([
            modele("models/fake-model-b", "Fake B", ["generateContent", "countTokens"], reflechit=False),
            modele("models/fake-model-a", "Fake A", ["generateContent", "countTokens"]),
            modele("models/fake-pro", "Fake Pro", ["generateContent"], sortie=None),
            modele("models/fake-embedding", "Fake Embedding", ["embedContent"], reflechit=None),
        ])

    def generate_content_stream(self, model, contents, config):
        """The answer of generate_content, cut into pieces. An error of generate_content is raised at the first piece."""
        reponse = self.generate_content(model, contents, config)
        texte = reponse.text
        morceaux = [texte[i:i + self.piece_size] for i in range(0, len(texte), self.piece_size)] or [""]
        if self.first_piece_delay:
            threading.Event().wait(self.first_piece_delay)  # a real wait: time.sleep is replaced in the tests
        if config.get("thinking_config") is not None:
            for numero in range(self.thinking_pieces):
                yield FakeMorceau(pensees=f"thought {numero}: reading the slides")
        for numero, morceau in enumerate(morceaux):
            if self.stall_after is not None and numero >= self.stall_after:
                raise httpx.ReadTimeout("timed out")
            yield FakeMorceau(morceau)
        while self.endless:
            yield FakeMorceau(" ")

    def upload(self, file):
        if self.upload_times_out:
            raise httpx.ReadTimeout("timed out")
        name = os.path.basename(file)
        self.uploads.append(name)
        if self.failed_files:
            etat_initial = etat("FAILED")
        else:
            etat_initial = etat("PROCESSING") if self.processing_polls else None
        return FakeFichier(f"<uploaded:{name}>", f"files/{name}", etat_initial)

    def get(self, name):
        self.polls += 1
        nom_fichier = name.split("/", 1)[1]
        pret = self.polls > self.processing_polls
        return FakeFichier(f"<uploaded:{nom_fichier}>", name, etat("ACTIVE") if pret else etat("PROCESSING"))

    def delete(self, name):
        if self.delete_fails:
            raise RuntimeError("delete failed (fake)")
        self.deleted.append(name)


class FakeKatex:
    """Stands in for katex_validator.valider_formules, so that the tests need no Node.js.

    A formula is refused when it contains one of the markers. Every call is recorded."""

    def __init__(self, markers=()):
        self.markers = markers
        self.calls = []

    def __call__(self, formules):
        self.calls.append(list(formules))
        return ["fake KaTeX error" if any(m in expression for m in self.markers) else None
                for expression, display in formules]


# Three statements written as bare formulas, without dollar signs: the failure that was seen in a Notion page.
ENONCES_LATEX_BRUT = [
    r"\widehat{\beta} = \arg\min_{u \in \mathbb{R}^p} \|Y - Xu\|^2",
    r"\widehat{\beta} = (X^\top X)^{-1} X^\top Y",
    r"\widehat{Y} = X\widehat{\beta} = P_X Y, \quad P_X = X(X^\top X)^{-1}X^\top, \quad \widehat{\varepsilon} = Y - \widehat{Y} = (I - P_X)Y",
]


# ---------------------------------------------------------------- invented answers

# Ordinary (non raw) string on purpose: "\n" followed by "eq" is what a JSON reader produces
# when the model forgets to double the backslash of \neq.
DAMAGED_NEQ = "$a\neq b$"
LONG_EQUATION = "$$" + " + ".join(f"\\frac{{a_{i}}}{{b_{i}}}" for i in range(180)) + "$$"
LONG_CODE = "\n".join(f"x{i} <- x{i - 1} + {i}  # step {i}" for i in range(1, 160))


def cours_answer():
    return {
        "titre_cours": "Chapter 1: Ridge regression",
        "nom_du_cours_notion": "Machine Learning",
        "fil_conducteur": (
            "The course asks how to fit a linear model when the data is noisy. It starts from least squares, "
            "shows why it can overfit, and adds a penalty $\\lambda$ to control the model."
        ),
        "parties": [
            {
                "titre_partie": "Penalising least squares",
                "introduction": (
                    "Least squares overfits when there are many features. This part adds a penalty to the loss, "
                    "which gives the ridge estimator, and measures what the penalty costs."
                ),
                "elements": [
                    {
                        "type_element": "Définition",
                        "nom": "Ridge estimator",
                        "enonce_latex": (
                            "Let $\\lambda > 0$ and $2n$ observations, with a budget of $50 per run and $60 in total.\n"
                            "(1) $X \\in \\mathbb{R}^{n \\times p}$\n"
                            "(2) $\\hat\\beta = (X^T X + \\lambda I)^{-1} X^T y$\n"
                            "(3) " + DAMAGED_NEQ + "\n"
                            "(or infinity if no largest value exists)."
                        ),
                        "explication": (
                            "Here $X$ holds the $n$ observations of $p$ features and $y$ the targets. The term $\\lambda I$ "
                            "is the brake: the larger $\\lambda$ is, the more the coefficients are pulled toward zero. "
                            "With $\\lambda = 0$ we get back least squares."
                        ),
                        "demonstration_utile": False,
                        "demonstration_latex": None,
                    },
                    {
                        "type_element": "Théorème",
                        "nom": "Bias of the ridge estimator",
                        "enonce_latex": "$$\\mathbb{E}[\\hat\\beta] \\neq \\beta$$",
                        "explication": "The brake has a price: on average the estimate is no longer centred on the true value $\\beta$.",
                        "demonstration_utile": True,
                        "demonstration_latex": (
                            "We expand the expectation.\n"
                            "$$\\begin{align} a &= b \\\\ c &= d \\end{align}$$\n"
                            + LONG_EQUATION
                        ),
                    },
                ],
            },
            {
                "titre_partie": "Choosing the penalty",
                "introduction": "The penalty $\\lambda$ must now be chosen. This part explains the trade-off behind that choice.",
                "elements": [
                    {
                        "type_element": "Concept clé",
                        "nom": "Bias-variance trade-off",
                        "enonce_latex": "",
                        "explication": "A simple model misses part of the truth but changes little with the data. A flexible one is the opposite.",
                        "demonstration_utile": False,
                        "demonstration_latex": None,
                    },
                ],
            },
        ],
    }


def exercices_answer():
    return {
        "titre_sujet": "Exercise sheet 1: ridge existence and bias",
        "nom_du_cours_notion": "Machine Learning",
        "exercices": [
            {
                "numero_exo": "Exercise 1",
                "enonce_latex": "Show that $X^T X + \\lambda I$ is invertible for $\\lambda > 0$.",
                "correction_etofee_latex": "The matrix is positive definite.\n$$v^T (X^T X + \\lambda I) v > 0$$",
            },
            {
                "numero_exo": "Exercise 2",
                "enonce_latex": "Compute the bias of $\\hat\\beta$.",
                "correction_etofee_latex": "$\\mathbb{E}[\\hat\\beta] - \\beta = -\\lambda (X^T X + \\lambda I)^{-1} \\beta$",
            },
        ],
        "methode_a_retenir": [
            "Write the penalized matrix and test positive definiteness.",
            "Use $\\hat\\beta = (X^T X + \\lambda I)^{-1} X^T y$ for the bias.",
        ],
    }


def lab_answer():
    return {
        "titre_lab": "Lab 1: ridge regression in R",
        "nom_du_cours_notion": "Statistics & Probability in R",
        "langage_principal": "R",  # upper case on purpose: the validator lower-cases it
        "parties": [
            {
                "titre_question": "Question 1: vectorized ridge",
                "enonce_ou_contexte": "Implement the estimator without a loop.",
                "justification_theorique_latex": "$\\nabla L(\\beta) = -2X^T(y - X\\beta) + 2\\lambda\\beta$",
                "langage_code": "R",
                "code_solution_propre": LONG_CODE,
                "explication_code": "The solve call avoids an explicit inverse. Complexity is cubic in $p$.",
            },
            {
                "titre_question": "Question 2: prediction",
                "enonce_ou_contexte": "Predict on new data.",
                "justification_theorique_latex": None,
                "langage_code": "python",
                "code_solution_propre": "def predict(X, beta):\n    return X @ beta",
                "explication_code": "One matrix product.",
            },
        ],
        "reflexes_et_astuces_code": [
            "Prefer solve over inv.",
            "Check dimensions with $n \\times p$ before multiplying.",
        ],
    }


def notes_answer():
    return {
        "titre_note": "Gradient of the ridge loss",
        "nom_du_cours_notion": "Probability Refresher",  # replaced by the course of the slot when it is known
        "chapitre_associe": "Penalized regression",
        "sections": [
            {
                "titre_section": "Statement and context",
                "contenu_latex": "We minimize $L(\\beta) = \\|y - X\\beta\\|^2 + \\lambda\\|\\beta\\|^2$.",
            },
            {
                "titre_section": "Step by step derivation",
                "contenu_latex": "$$\\nabla L(\\beta) = -2X^T(y - X\\beta) + 2\\lambda\\beta$$\nSetting it to zero gives the estimator.",
            },
        ],
        "remarques_importantes": [
            "The penalty shrinks $\\beta$ toward zero.",
            "Do not confuse $\\lambda$ with an eigenvalue.",
        ],
    }


ANSWERS = {
    "FicheCours": cours_answer,
    "FicheExercices": exercices_answer,
    "FicheLab": lab_answer,
    "FicheNotesManuscrites": notes_answer,
}


# ---------------------------------------------------------------- invented input files

def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _png(path):
    """A fake photo: the program never decodes it, it only reads its name and uploads it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake image bytes")


def _notebook(language, cells):
    return json.dumps({
        "metadata": {"language_info": {"name": language}},
        "cells": [{"cell_type": kind, "source": [text]} for kind, text in cells],
    })


def build_sample(drive):
    """Create a small Drive tree. Correction files use the exact spelling "_Correction" so that
    the result is the same on case sensitive (Linux) and case insensitive (macOS) file systems."""
    drive = Path(drive)
    todo = drive / "01_A_Traiter"
    (drive / "02_Archives").mkdir(parents=True, exist_ok=True)

    _write(todo / "Cours" / "chapter1.pdf", "Chapter 1: Ridge regression (sample text)")
    _write(todo / "Exercices" / "sheet1.pdf", "Exercise sheet 1 (sample text)")
    _write(todo / "Corriges" / "sheet1_Correction.pdf", "Correction of sheet 1 (sample text)")
    _write(todo / "Exercices" / "sheet2.pdf", "Exercise sheet 2, no correction yet")

    _write(todo / "Labs" / "lab1.ipynb", _notebook("python", [("markdown", "Question 1. Implement ridge regression.")]))
    _write(todo / "Labs_Corriges" / "lab1_Correction.ipynb", _notebook("R", [("code", "ridge <- function(X, y) solve(t(X) %*% X, t(X) %*% y)")]))
    _write(todo / "Labs" / "lab2.ipynb", _notebook("python", [("markdown", "Waiting for its correction.")]))
    _write(todo / "Labs" / "lab3.rmd", "---\ntitle: Lab 3\n---\n\nQuestion 1. Simulate 100 draws.\n")
    _write(todo / "Labs_Corriges" / "lab3_Correction.rmd", "---\ntitle: Lab 3 correction\n---\n\nx <- rnorm(100)\n")

    # 2026-10-19 is a Monday, 2026-10-20 a Tuesday, 2026-10-23 a Friday
    notes = todo / "Notes_Manuscrites"
    _png(notes / "2026-10-19_10-15-00.png")
    _png(notes / "2026-10-19_10-40-00.png")   # same session as the previous photo
    _png(notes / "2026-10-19_15-00-00.png")   # Monday afternoon
    _png(notes / "2026-10-23_15-30-00.png")   # Friday afternoon
    _png(notes / "2026-10-20_11-00-00.png")   # Tuesday: outside the timetable
    _png(notes / "photo_without_date.png")    # no date in the name: left in place


def list_files(base):
    base = Path(base)
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())


# ---------------------------------------------------------------- environment

def _read_text(path):
    return Path(path).read_text(encoding="utf-8")


@contextlib.contextmanager
def environment(notion=None, gemini=None, katex=None):
    """Replace every outside service and every setting. Nothing real is read, so it is also
    safe on a machine that has its own .env file."""
    notion = notion or FakeNotion()
    gemini = gemini or FakeGemini()
    katex = katex or FakeKatex()
    pauses = []  # the waits between two tries of a model, recorded instead of slept
    with tempfile.TemporaryDirectory() as tmp:
        drive = Path(tmp) / "drive"
        drive.mkdir()
        patches = [
            (settings, "NOTION_TOKEN", FAKE_NOTION_TOKEN),
            (settings, "NOTION_DATABASE_ID", FAKE_DATABASE_ID),
            (settings, "GEMINI_API_KEY", FAKE_GEMINI_KEY),
            (settings, "DRIVE_ROOT", str(drive)),
            (settings, "GEMINI_MODELS", list(FAKE_MODELS)),
            (notion_api, "httpx", notion),
            (gemini_client, "_client", gemini),
            (gemini_client, "_modeles_sans_pensees", set()),
            (katex_validator, "valider_formules", katex),
            (gemini_client.time, "sleep", pauses.append),
            (sources, "pdf_vers_markdown", _read_text),
        ]
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))  # keep the test report readable
            for target, name, value in patches:
                stack.enter_context(mock.patch.object(target, name, value))
            yield types.SimpleNamespace(drive=drive, notion=notion, gemini=gemini, katex=katex, pauses=pauses)


def run_pipeline():
    """Run the whole pipeline and return what it printed."""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        pipeline.lancer_pipeline()
    return printed.getvalue()
