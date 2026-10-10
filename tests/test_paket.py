"""Paket als Ganzes: keine Vorgabe auf interne Adressen, README und Alias-Namen passen zusammen."""

from __future__ import annotations

import re
from pathlib import Path

from fyos_plugin_finanzen import _umgebung

WURZEL = Path(__file__).resolve().parent.parent

# Adressen, die zu einer bestimmten Installation gehören: Rechner im LAN, Dienste im Cluster, Namen im Heimnetz.
_INTERN = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
    r"|\.svc\.cluster\.local|\.fyos\.lan|\.intern\b"
)
_TEXT = {".py", ".yaml", ".yml", ".md", ".toml", ".json", ".sh", ".plist", ".scpt", ".applescript", ".lua", ".txt", ".cfg"}
_UEBERSPRINGEN = {".git", "__pycache__", ".venv", "build", "dist", ".pytest_cache"}


def test_paket_nennt_keine_internen_adressen():
    """Kein Code, Text, Beispiel und Test zeigt auf LAN-Adressen oder Dienste im Cluster (Tests: example.org)."""
    funde = []
    for datei in sorted(WURZEL.rglob("*")):
        if not datei.is_file() or datei.suffix not in _TEXT or _UEBERSPRINGEN & set(datei.relative_to(WURZEL).parts):
            continue
        if datei.name == Path(__file__).name:
            continue  # dieser Test nennt die Muster selbst
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _INTERN.search(zeile):
                funde.append(f"{datei.relative_to(WURZEL)}:{nr}: {zeile.strip()[:100]}")
    assert not funde, "\n".join(funde)


def test_readme_nennt_die_neuen_namen_die_alten_nur_in_der_tabelle():
    readme = (WURZEL / "README.md").read_text(encoding="utf-8")
    haupt, _, alte_namen = readme.partition("### Alte Namen")
    assert alte_namen, "die README braucht die Tabelle „Alte Namen“"
    for neu, alte in _umgebung.ALIAS.items():
        assert neu.startswith("FYOS_"), neu
        assert f"`{neu}`" in haupt and f"`{neu}`" in alte_namen, neu
        for alt in alte:
            assert alt not in haupt, f"{alt} gehört nur in die Tabelle „Alte Namen“"
            assert f"`{alt}`" in alte_namen, alt
    for neu, alte in _umgebung.CONFIG_ALIAS.items():
        assert f"`{neu}`" in haupt and f"`{neu}`" in alte_namen, neu
        for alt in alte:
            assert f"`{alt}`" in alte_namen, alt
