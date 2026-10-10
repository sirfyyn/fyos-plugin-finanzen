"""Einstellungen aus der Umgebung des Hubs: neue Namen `FYOS_<BEREICH>_<WAS>`, die alten Namen gelten als Alias.

Reihenfolge: ausdrückliche Einstellung vor Vorgabe. Der neue Name gewinnt über seine Aliase; leere Werte zählen als nicht
gesetzt. Vorgaben auf Dienste einer bestimmten Installation gibt es nicht: Ist nichts gesetzt, ist die Fähigkeit
„nicht eingerichtet“ und sagt, wie man sie einrichtet.
"""

from __future__ import annotations

import os

#: neuer Name → alte Namen (Alias). Der neue Name steht in der README, die alten laufen weiter.
ALIAS: dict[str, tuple[str, ...]] = {
    "FYOS_FINANZEN_KONTEN_URL": ("FYOS_FINANZEN_LIFE_URL",),
}

#: Schlüssel in der Plugin-Konfiguration (`config:` im Hub): neuer Schlüssel → alte Schlüssel
CONFIG_ALIAS: dict[str, tuple[str, ...]] = {
    "konten_url": ("life_url",),
}


def lesen(name: str) -> str:
    """Wert der Einstellung `name` (neuer Name), sonst ihres Alias; ohne beide ein leerer Text."""
    for kandidat in (name, *ALIAS.get(name, ())):
        wert = (os.environ.get(kandidat) or "").strip()
        if wert:
            return wert
    return ""


def aus_config(config: dict, schluessel: str) -> str:
    """Wert des Schlüssels in der Plugin-Konfiguration (neuer Schlüssel, sonst sein Alias); leer = nicht gesetzt."""
    for kandidat in (schluessel, *CONFIG_ALIAS.get(schluessel, ())):
        wert = str(config.get(kandidat) or "").strip()
        if wert:
            return wert
    return ""
