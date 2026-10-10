"""finance-Plugin: Schreibwege und Lesesichten (Netz ist eine Attrappe).

Ohne Adresse der Dienste ist jedes Werkzeug „nicht eingerichtet“ (kein Fehler, keine Anfrage ins Leere); die alten Namen
(FYOS_FINANZEN_LIFE_URL, life_url) gelten als Alias, der neue Name gewinnt.
"""

from __future__ import annotations

import datetime as dt
import json
from importlib.metadata import entry_points

import pytest

from fyos_plugin_finanzen import _umgebung
from fyos_plugin_finanzen import plugin as fin

FINANZEN_URL = "http://finanzen.example.org"  # Dokumentationsadressen; das Netz ist immer eine Attrappe
KONTEN_URL = "http://konten.example.org"
ALLE_NAMEN = sorted({"FYOS_FINANZEN_URL", "FYOS_FINANZEN_AUTH_HEADER", *_umgebung.ALIAS,
                     *(a for alt in _umgebung.ALIAS.values() for a in alt)})


@pytest.fixture(autouse=True)
def _ohne_einstellungen(monkeypatch):
    """Jeder Test beginnt ohne jede Einstellung (neue und alte Namen) und ohne Kopfzeile von einem früheren Test."""
    for name in ALLE_NAMEN:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(fin, "_AUTH", {})


def _plugin(**kw):
    """Ein eingerichtetes Plugin: beide Dienste sind per config: gesetzt."""
    kw.setdefault("config", {"finanzen_url": FINANZEN_URL, "konten_url": KONTEN_URL})
    return fin.FinancePlugin(**kw)


def _ohne(**kw):
    """Ein Plugin ohne jede Einstellung."""
    return fin.FinancePlugin(**kw)


def test_finance_posten_bezahlt():
    p = _plugin()
    gesendet = []
    p._senden = lambda basis, m, pfad, k: gesendet.append((m, pfad, k)) or {"ok": True}
    p.call("posten_bezahlt", {"id": "42", "betrag": 900, "monat": "2026-10"})
    assert gesendet == [("PATCH", "/api/finance/month/42?month=2026-10",
                         {"status": "bezahlt", "note": "Zentrale", "betrag_actual": 900})]
    with pytest.raises(ValueError):
        p.call("posten_bezahlt", {"id": "../x"})


def _aussetzen_plugin(ziel_posten):
    """Attrappe: Oktober hat Rate 100 (offen), Nachhol-Monat liefert `ziel_posten`."""
    p = _plugin()
    gesendet = []
    p._senden = lambda basis, m, pfad, k: gesendet.append((m, pfad, k)) or {"ok": True}

    def holen(basis, pfad, timeout=8.0):
        posten = {"id": "42", "posten": "Miete", "betrag": 100, "status": "offen", "manuell": False}
        if pfad.endswith("2026-11"):
            posten = {**posten, **ziel_posten}
        return {"month": pfad[-7:], "obligations": [posten], "income": [], "summary": {}}

    p._holen = holen
    return p, gesendet


def test_finance_posten_aussetzen_und_nachholen():
    p, gesendet = _aussetzen_plugin({"status": "geplant"})
    p.call("monat", {"monat": "2026-10"})  # fuellt den 20-s-Speicher
    p.call("posten_aussetzen", {"id": "42", "monat": "2026-10", "nachholen_in": "2026-11"})
    assert gesendet == [
        ("PATCH", "/api/finance/month/42?month=2026-10",
         {"status": "ausgesetzt", "note": "ausgesetzt 2026-10, nachgeholt"}),
        ("PATCH", "/api/finance/month/42?month=2026-11",
         {"status": "auto", "betrag_actual": 200.0, "note": "ausgesetzt 2026-10, nachgeholt"}),
    ]
    assert p._frisch._d == {}  # Speicher geleert


def test_finance_posten_aussetzen_baut_auf_bestehendem_soll_auf_und_ist_idempotent():
    p, gesendet = _aussetzen_plugin({"status": "geplant", "soll": 150})
    p.call("posten_aussetzen", {"id": "42", "monat": "2026-10", "nachholen_in": "2026-11"})
    assert gesendet[1][2]["betrag_actual"] == 250.0
    p, gesendet = _aussetzen_plugin(
        {"status": "geplant", "soll": 200, "note": "ausgesetzt 2026-10, nachgeholt"}
    )
    r = p.call("posten_aussetzen", {"id": "42", "monat": "2026-10", "nachholen_in": "2026-11"})
    assert len(gesendet) == 1 and r["nachgeholt"]["schon_erledigt"]  # nur die Aussetzung, kein 3 x


def test_finance_posten_aussetzen_ohne_nachholen_und_pruefungen():
    p, gesendet = _aussetzen_plugin({})
    p.call("posten_aussetzen", {"id": "42", "monat": "2026-10"})
    assert len(gesendet) == 1 and gesendet[0][2]["status"] == "ausgesetzt"
    for falsch in (
        {"id": "../x", "monat": "2026-10"},
        {"id": "42"},
        {"id": "42", "monat": "2026-10", "nachholen_in": "2026-09"},
        {"id": "42", "monat": "2026-10", "nachholen_in": "2026-10"},
        {"id": "42", "monat": "2026-10", "nachholen_in": "Nov"},
    ):
        with pytest.raises(ValueError):
            p.call("posten_aussetzen", falsch)
    p, gesendet = _aussetzen_plugin({"status": "bezahlt"})
    with pytest.raises(ValueError, match="bezahlt"):
        p.call("posten_aussetzen", {"id": "42", "monat": "2026-10", "nachholen_in": "2026-11"})
    assert gesendet == []  # erst pruefen, dann schreiben
    d = {t.name: t for t in _plugin().describe_tools()}["posten_aussetzen"]
    assert set(d.tags) >= {"write", "flaeche"}


MONAT_ANTWORT = {
    "month": "2026-10",
    "is_current": True,
    "obligations": [
        {"id": "a1", "posten": "Miete", "betrag": 900, "faelligkeit": "2026-10-01", "status": "bezahlt",
         "manuell": False, "intervall": "monatlich", "typ": "fix", "restschuld": None, "iban": "DE00 1234",
         "referenz": "Vermieter", "notizen": "geheim", "match": {"betrag_diff": 0, "via": "referenz", "datum": "2026-10-01"}},
        {"id": "a2", "posten": "Kredit", "betrag": "250", "faelligkeit": "2026-10-01", "status": "offen",
         "manuell": False, "intervall": "monatlich", "typ": "kredit", "restschuld": 1200.5, "iban": "DE99", "match": None},
        {"id": "a3", "posten": "Versicherung", "betrag": 80, "faelligkeit": "2026-11-15", "status": "ruht",
         "manuell": False, "intervall": "jaehrlich", "typ": None, "restschuld": None, "match": None},
    ],
    "income": [
        {"id": "e1", "quelle": "Gehalt", "betrag": 3800, "status": "erhalten", "manuell": False,
         "match": {"datum": "2026-09-30"}},
        {"id": "e2", "quelle": "Kindergeld", "betrag": 255, "status": "ausstehend", "manuell": False, "match": None},
    ],
    "summary": {"ausgaben_geplant": 1150, "ausgaben_bezahlt": 900, "restschuld_total": 1200.5, "ueberfaellig": ["Kredit"],
                "einnahmen_geplant": 4055, "einnahmen_erhalten": 3800, "einnahmen_ausstehend": ["Kindergeld"], "saldo": 2905},
}


def test_finance_monat_liefert_alle_posten_mit_status_ohne_iban():
    p = _plugin()
    gefragt = []
    p._holen = lambda basis, pfad, timeout=8.0: gefragt.append(pfad) or MONAT_ANTWORT
    m = p.call("monat", {"monat": "2026-10"})
    assert gefragt == ["/api/finance/month?month=2026-10"]
    assert [(x["id"], x["status"]) for x in m["posten"]] == [("a1", "bezahlt"), ("a2", "offen"), ("a3", "ruht")]
    assert m["posten"][0]["bezahlt_am"] == "2026-10-01" and m["posten"][1]["betrag"] == 250.0
    assert m["posten"][1]["restschuld"] == 1200.5 and m["posten"][2]["restschuld"] is None
    assert [(e["name"], e["status"]) for e in m["einnahmen"]] == [("Gehalt", "erhalten"), ("Kindergeld", "ausstehend")]
    assert m["summe"]["restschuld"] == 1200.5 and m["summe"]["saldo"] == 2905.0
    assert m["ueberfaellig"] == ["Kredit"] and m["einnahmen_ausstehend"] == ["Kindergeld"]
    assert m["monat"] == "2026-10" and m["aktuell"] is True
    roh = repr(m)
    for geheim in ("DE00", "DE99", "Vermieter", "geheim"):
        assert geheim not in roh
    p.call("monat", {"monat": "2026-10"})  # 20 s frisch: kein zweiter Abruf
    assert len(gefragt) == 1
    p._senden = lambda *a: {"ok": True}  # bezahlt vergisst die Sicht
    p.call("posten_bezahlt", {"id": "a2", "monat": "2026-10"})
    p.call("monat", {"monat": "2026-10"})
    assert len(gefragt) == 2


@pytest.mark.parametrize("falsch", [{"monat": "Oktober"}, {"monat": "2026-1"}, {"monat": 202610}])
def test_finance_monat_prueft_den_monat(falsch):
    p = _plugin()
    p._holen = lambda *a, **k: MONAT_ANTWORT
    with pytest.raises(ValueError):
        p.call("monat", falsch)


def test_finance_monat_ohne_obligations_ist_fehler_nicht_leer():
    p = _plugin()
    p._holen = lambda *a, **k: {"detail": "Not Found"}
    with pytest.raises(ValueError, match="obligations"):
        p.call("monat", {})


def test_lesesicht_ist_lesend_mit_flaeche():
    d = {t.name: t for t in _plugin().describe_tools()}["monat"]
    assert set(d.tags) >= {"read", "flaeche"} and "write" not in d.tags


KONTEN = {
    "stand": "2026-10-05T14:00+00:00",
    "konten": [
        {"uuid": "g", "name": "Alle", "typ": "Kontengruppe", "saldo": 999.0, "gruppe": True},
        {"uuid": "a", "name": "N26 Giro", "typ": "Girokonto", "saldo": 120.5, "waehrung": "EUR", "iban_ende": "1234"},
        {"uuid": "b", "name": "Tagesgeld", "typ": "Sparkonto", "saldo": 1000.0, "waehrung": "EUR"},
    ],
}
SUMMARY = {
    "summary": {"days": 30},
    "transactions": [
        {"amount": -42.1, "merchant": "REWE", "purpose": "Einkauf", "category": "Lebensmittel", "account": "a",
         "booking_date": "2026-10-04"},
        {"amount": 2500.0, "merchant": "Arbeitgeber", "purpose": "Gehalt", "category": "Einkommen", "account": "a",
         "booking_date": "2026-10-01"},
        {"amount": -9.99, "merchant": "Spotify", "purpose": "Abo", "category": "Streaming/Abo", "account": "b",
         "booking_date": "2026-09-30"},
    ],
}


def _mm_plugin():
    p = _plugin()
    gefragt = []

    def holen(basis, pfad, timeout=8.0):
        gefragt.append(pfad)
        return KONTEN if pfad.endswith("finance-konten") else SUMMARY

    p._holen = holen
    return p, gefragt


def test_konten_summe_ohne_gruppen_und_alter():
    s = fin.kontensicht(KONTEN, dt.datetime(2026, 10, 5, 16, tzinfo=dt.timezone.utc))
    assert s["summe"] == {"EUR": 1120.5} and s["alter_h"] == 2.0
    assert len(s["konten"]) == 3 and "2 Konten" in s["satz"]
    alt = fin.kontensicht(KONTEN, dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc))
    assert "veraltet" in alt["satz"]
    assert "Noch keine" in fin.kontensicht({"konten": [], "stand": None})["satz"]


def test_umsaetze_filter_kontonamen_und_summen():
    p, gefragt = _mm_plugin()
    r = p.call("umsaetze", {})
    assert r["anzahl"] == 3 and r["summe"] == {"ein": 2500.0, "aus": 52.09, "saldo": 2447.91}
    assert r["umsaetze"][0]["konto"] == "N26 Giro" and "/finance-summary?days=30" in gefragt[0]
    assert p.call("umsaetze", {"suche": "rewe"})["anzahl"] == 1
    assert p.call("umsaetze", {"konto": "tages"})["umsaetze"][0]["gegenueber"] == "Spotify"
    assert p.call("umsaetze", {"kategorie": "einkommen", "limit": 1})["gezeigt"] == 1
    for falsch in ({"tage": 0}, {"tage": 91}, {"limit": True}, {"tage": "30"}, {"sortierung": "alt"}):
        with pytest.raises(ValueError):
            p.call("umsaetze", falsch)


def test_umsaetze_groesste_ausgaben_zuerst():
    p, _ = _mm_plugin()
    r = p.call("umsaetze", {"sortierung": "ausgaben"})
    assert [t["gegenueber"] for t in r["umsaetze"]] == ["REWE", "Spotify"]
    assert r["anzahl"] == 3 and r["gezeigt"] == 2 and r["summe"]["ein"] == 2500.0


def test_moneymoney_werkzeuge_nur_lesend():
    d = {t.name: t for t in _plugin().describe_tools()}
    for name in ("konten", "umsaetze"):
        assert "read" in d[name].tags and "write" not in d[name].tags


def test_endpunkte_aus_config_dann_umgebung(monkeypatch):
    monkeypatch.setenv("FYOS_FINANZEN_URL", "http://env.example.org/")
    assert _ohne().finanzen_url == "http://env.example.org"
    assert _ohne(config={"finanzen_url": "http://cfg.example.org"}).finanzen_url == "http://cfg.example.org"
    assert _ohne().konten_url == "" and _ohne().life_url == "", "keine Vorgabe auf einen Dienst"
    p = _ohne(config={"finanzen_url": "http://cfg.example.org"})
    basen = []
    p._holen = lambda basis, pfad, timeout=8.0: basen.append(basis) or MONAT_ANTWORT
    p.call("monat", {"monat": "2026-10"})
    assert basen == ["http://cfg.example.org"]


def test_kopfzeile_aus_auth_header():
    assert fin._kopfzeilen("X-Test: wert")["X-Test"] == "wert"
    assert fin._kopfzeilen("")["Accept"] == "application/json"


def test_auth_header_hat_keine_vorgabe_config_geht_vor_umgebung(monkeypatch):
    _ohne().register()
    assert fin._AUTH["h"] == "" and fin._kopfzeilen(fin._AUTH["h"]) == {"Accept": "application/json"}
    monkeypatch.setenv("FYOS_FINANZEN_AUTH_HEADER", "X-Test: umgebung")
    _ohne().register()
    assert fin._AUTH["h"] == "X-Test: umgebung"
    _ohne(config={"auth_header": "X-Test: config"}).register()
    assert fin._AUTH["h"] == "X-Test: config"


# ── Ohne Einstellung: „nicht eingerichtet“ ───────────────────────────────────
def _leer():
    """Plugin ohne Einstellung, dessen Netzwege nie gerufen werden dürfen."""

    def verboten(*a, **k):
        raise AssertionError("ohne Adresse darf keine Anfrage hinausgehen")

    p = _ohne()
    p._holen = verboten
    p._senden = verboten
    return p


@pytest.mark.parametrize(
    "werkzeug, args, variable",
    [
        ("monat", {}, "FYOS_FINANZEN_URL"),
        ("monat", {"monat": "2026-10"}, "FYOS_FINANZEN_URL"),
        ("konten", {}, "FYOS_FINANZEN_KONTEN_URL"),
        ("umsaetze", {}, "FYOS_FINANZEN_KONTEN_URL"),
        ("umsaetze", {"tage": 7, "sortierung": "ausgaben"}, "FYOS_FINANZEN_KONTEN_URL"),
    ],
)
def test_lesende_werkzeuge_ohne_adresse_nennen_ihren_zustand_und_den_weg(werkzeug, args, variable):
    r = _leer().call(werkzeug, args)
    assert r["zustand"] == "nicht eingerichtet" and r["eingerichtet"] is False
    assert variable in r["einrichten"] and "nicht eingerichtet" in r["satz"]
    assert not {"posten", "einnahmen", "konten", "umsaetze", "error", "ok"} & set(r), "weder Fehler noch leere Liste"
    json.dumps(r)


@pytest.mark.parametrize(
    "werkzeug, args",
    [
        ("posten_bezahlt", {"id": "42"}),
        ("posten_bezahlt", {"id": "42", "betrag": 12.5, "monat": "2026-10"}),
        ("posten_aussetzen", {"id": "42", "monat": "2026-10"}),
        ("posten_aussetzen", {"id": "42", "monat": "2026-10", "nachholen_in": "2026-11"}),
    ],
)
def test_schreibende_werkzeuge_ohne_adresse_lehnen_mit_dem_weg_ab(werkzeug, args):
    r = _leer().call(werkzeug, args)
    assert r["ok"] is False and r["zustand"] == "nicht eingerichtet" and r["eingerichtet"] is False
    assert "FYOS_FINANZEN_URL" in r["einrichten"] and "FYOS_FINANZEN_URL" in r["error"]
    assert "nichts geändert" in r["error"]


def test_ungueltige_argumente_werden_auch_ohne_adresse_abgewiesen():
    p = _leer()
    for werkzeug, falsch in (
        ("monat", {"monat": "Oktober"}),
        ("posten_bezahlt", {"id": "../x"}),
        ("posten_aussetzen", {"id": "42"}),
        ("umsaetze", {"tage": 0}),
    ):
        with pytest.raises(ValueError):
            p.call(werkzeug, falsch)
    with pytest.raises(ValueError, match="kein Werkzeug"):
        p.call("gibt-es-nicht", {})


def test_ist_nur_ein_dienst_eingerichtet_sagt_der_andere_was_fehlt():
    p = _ohne(config={"finanzen_url": FINANZEN_URL})
    p._holen = lambda basis, pfad, timeout=8.0: MONAT_ANTWORT
    assert p.call("monat", {"monat": "2026-10"})["monat"] == "2026-10"
    assert p.call("konten", {})["zustand"] == "nicht eingerichtet"
    q = _ohne(config={"konten_url": KONTEN_URL})
    q._holen = lambda basis, pfad, timeout=8.0: {"konten": [], "stand": None}
    assert q.call("konten", {})["konten"] == []
    assert q.call("monat", {})["zustand"] == "nicht eingerichtet"
    assert q.call("posten_bezahlt", {"id": "42"})["ok"] is False


def test_health_nicht_eingerichtet_teilweise_und_ok():
    h = _ohne().health()
    assert h["ok"] is True and h["status"] == "nicht eingerichtet"
    assert "FYOS_FINANZEN_URL" in h["einrichten"] and "FYOS_FINANZEN_KONTEN_URL" in h["einrichten"]
    assert h["finanzen_url"] == "" and h["konten_url"] == ""
    h = _ohne(config={"finanzen_url": FINANZEN_URL}).health()
    assert h["ok"] is True and h["status"] == "teilweise eingerichtet" and "FYOS_FINANZEN_KONTEN_URL" in h["einrichten"]
    h = _ohne(config={"konten_url": KONTEN_URL}).health()
    assert h["status"] == "teilweise eingerichtet" and "FYOS_FINANZEN_URL" in h["einrichten"]
    h = _plugin().health()
    assert h["ok"] is True and h["status"] == "ok" and "einrichten" not in h
    assert (h["finanzen_url"], h["konten_url"]) == (FINANZEN_URL, KONTEN_URL)


def test_alte_namen_gelten_als_alias_der_neue_gewinnt(monkeypatch):
    monkeypatch.setenv("FYOS_FINANZEN_LIFE_URL", "http://alt.example.org/")
    assert _ohne().konten_url == "http://alt.example.org" and _ohne().life_url == "http://alt.example.org"
    monkeypatch.setenv("FYOS_FINANZEN_KONTEN_URL", "http://neu.example.org")
    assert _ohne().konten_url == "http://neu.example.org"
    monkeypatch.setenv("FYOS_FINANZEN_KONTEN_URL", "  ")  # leer zählt als nicht gesetzt: dann gilt der Alias
    assert _ohne().konten_url == "http://alt.example.org"
    # Plugin-config: der neue Schlüssel gewinnt vor dem alten, und die config geht der Umgebung vor.
    monkeypatch.setenv("FYOS_FINANZEN_KONTEN_URL", "http://neu.example.org")
    assert _ohne(config={"life_url": "http://cfg-alt.example.org"}).konten_url == "http://cfg-alt.example.org"
    gesetzt = {"konten_url": "http://cfg-neu.example.org", "life_url": "http://cfg-alt.example.org"}
    assert _ohne(config=gesetzt).konten_url == "http://cfg-neu.example.org"


def test_der_alte_name_richtet_konten_und_umsaetze_ein(monkeypatch):
    monkeypatch.setenv("FYOS_FINANZEN_LIFE_URL", "http://alt.example.org")
    p = _ohne()
    basen = []

    def holen(basis, pfad, timeout=8.0):
        basen.append(basis)
        return KONTEN if pfad.endswith("finance-konten") else SUMMARY

    p._holen = holen
    assert p.call("konten", {})["summe"] == {"EUR": 1120.5}
    assert p.call("umsaetze", {})["anzahl"] == 3
    assert set(basen) == {"http://alt.example.org"}


def test_entry_point_ist_registriert():
    eps = {e.name: e.value for e in entry_points(group="mcp_hub.plugins")}
    assert eps.get("finance") == "fyos_plugin_finanzen.plugin:FinancePlugin"


def test_skill_reist_im_paket_mit():
    """Der Skill liegt im Paket; das Hub-Plugin `skills` findet ihn (Entry-Point `fyos.skills`)."""
    import tomllib
    from pathlib import Path

    from mcp_hub.plugins.skills import SkillsPlugin

    wurzel = Path(__file__).resolve().parent.parent
    daten = tomllib.loads((wurzel / "pyproject.toml").read_text(encoding="utf-8"))
    assert daten["project"]["entry-points"]["fyos.skills"]["finanzen"] == "fyos_plugin_finanzen"
    skills = SkillsPlugin({"quellen": [str(wurzel / "fyos_plugin_finanzen" / "skills")], "plugin_skills": False})
    treffer = skills.call("suchen", {"q": "kontostand umsätze"})
    assert [s["name"] for s in treffer["skills"]] == ["finanzen"]
    assert "finance.posten_aussetzen" in skills.call("lesen", {"name": "finanzen"})["text"]
