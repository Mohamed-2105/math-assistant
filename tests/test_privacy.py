"""Privacy tests: secrets are hidden in the output, and nothing private is in the repository.

The words that must never appear come from your private .env (PRIVATE_WORDS, separated by
commas), never from a file of the repository. Without that setting the word check is skipped.
"""
import contextlib
import io
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from dotenv import dotenv_values

import settings
from tests import fakes

ROOT = Path(__file__).resolve().parent.parent
SKIPPED_FOLDERS = {".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", "node_modules"}
SKIPPED_FILES = {".env", ".DS_Store"}

KEY_PATTERNS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "Notion token": re.compile(r"\b(?:secret_|ntn_)[A-Za-z0-9]{20,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PLACEHOLDER_DOMAINS = ("example.com", "example.org")


def project_files():
    for chemin in sorted(ROOT.rglob("*")):
        if not chemin.is_file():
            continue
        if SKIPPED_FOLDERS & set(chemin.relative_to(ROOT).parts) or chemin.name in SKIPPED_FILES:
            continue
        yield chemin


def private_words():
    brut = os.environ.get("PRIVATE_WORDS") or dotenv_values(ROOT / ".env").get("PRIVATE_WORDS") or ""
    return [w.strip().lower() for w in brut.split(",") if w.strip()]


class SecretsInOutputTest(unittest.TestCase):
    def test_log_hides_every_secret(self):
        with fakes.environment():
            sortie = io.StringIO()
            with contextlib.redirect_stdout(sortie):
                settings.log(f"token {fakes.FAKE_NOTION_TOKEN}, key {fakes.FAKE_GEMINI_KEY}, db {fakes.FAKE_DATABASE_ID}")
        texte = sortie.getvalue()
        for secret in (fakes.FAKE_NOTION_TOKEN, fakes.FAKE_GEMINI_KEY, fakes.FAKE_DATABASE_ID):
            self.assertNotIn(secret, texte)
        self.assertEqual(texte.count("***"), 3)

    def test_a_full_run_prints_no_secret(self):
        with fakes.environment(notion=fakes.FakeNotion(reject_all_posts=True)) as env:
            fakes.build_sample(env.drive)
            texte = fakes.run_pipeline()
        for secret in (fakes.FAKE_NOTION_TOKEN, fakes.FAKE_GEMINI_KEY):
            self.assertNotIn(secret, texte)

    def test_missing_settings_are_listed(self):
        with mock.patch.multiple(settings, NOTION_TOKEN=None, NOTION_DATABASE_ID="x", GEMINI_API_KEY="", DRIVE_ROOT="/x"):
            self.assertEqual(settings.reglages_manquants(), ["NOTION_TOKEN", "GEMINI_API_KEY"])


class RepositoryScanTest(unittest.TestCase):
    def test_no_key_like_string_in_any_file(self):
        for chemin in project_files():
            texte = chemin.read_text(encoding="utf-8", errors="ignore")
            for nom, motif in KEY_PATTERNS.items():
                with self.subTest(file=chemin.relative_to(ROOT).as_posix(), kind=nom):
                    self.assertIsNone(motif.search(texte))

    def test_no_real_looking_email_address(self):
        for chemin in project_files():
            texte = chemin.read_text(encoding="utf-8", errors="ignore")
            for adresse in EMAIL.findall(texte):
                with self.subTest(file=chemin.relative_to(ROOT).as_posix(), address=adresse):
                    self.assertTrue(adresse.lower().endswith(PLACEHOLDER_DOMAINS))

    def test_no_private_word_in_any_file_or_file_name(self):
        mots = private_words()
        if not mots:
            self.skipTest("PRIVATE_WORDS is not set in .env")
        for chemin in project_files():
            texte = (chemin.read_text(encoding="utf-8", errors="ignore") + " " + chemin.name).lower()
            for mot in mots:
                with self.subTest(file=chemin.relative_to(ROOT).as_posix()):
                    self.assertNotIn(mot, texte)

    def test_gitignore_keeps_the_private_files_out(self):
        lignes = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", lignes)

    def test_env_example_holds_placeholders_only(self):
        valeurs = dotenv_values(ROOT / ".env.example")
        self.assertEqual(set(valeurs), {"NOTION_TOKEN", "NOTION_DATABASE_ID", "GEMINI_API_KEY", "DRIVE_ROOT", "GEMINI_MODELS", "NIVEAU_REFLEXION", "BUDGET_REFLEXION", "PRIVATE_WORDS"})
        for nom in ("PRIVATE_WORDS", "NIVEAU_REFLEXION", "BUDGET_REFLEXION"):
            self.assertEqual(valeurs[nom], "", nom)
        for nom in ("NOTION_TOKEN", "NOTION_DATABASE_ID", "GEMINI_API_KEY"):
            self.assertTrue(valeurs[nom].startswith("your-"), nom)


if __name__ == "__main__":
    unittest.main()
