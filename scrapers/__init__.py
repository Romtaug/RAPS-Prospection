"""
Package scrapers - version RAPS (nettoyage Lyon / Rhône).

On ne garde que deux cibles : agences immobilières et notaires.
Les deux ont des locaux à entretenir, et les notaires gèrent des
successions (débarras / syndrome de Diogène).
"""

from scrapers.immo import ImmoScraper
from scrapers.notaires import NotairesScraper


REGISTRY = {
    "immo": ImmoScraper,
    "notaires": NotairesScraper,
}


__all__ = [
    "REGISTRY",
    "ImmoScraper",
    "NotairesScraper",
]
