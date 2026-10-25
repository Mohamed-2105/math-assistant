"""Entry point: python main.py"""
import sys

import settings
from pipeline import lancer_pipeline


def main():
    manquants = settings.reglages_manquants()
    if manquants:
        settings.log("Réglages manquants dans .env : " + ", ".join(manquants))
        settings.log("Copie .env.example vers .env puis remplis les valeurs.")
        return 1
    lancer_pipeline()
    return 0


if __name__ == "__main__":
    sys.exit(main())
