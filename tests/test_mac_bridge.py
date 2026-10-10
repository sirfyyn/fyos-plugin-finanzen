"""mac/moneymoney_csv_watch.py und moneymoney_import.py: keine Vorgabe auf Dienste, ohne Einstellung „nicht eingerichtet“."""

import importlib.util
import sys
from pathlib import Path

import pytest

MAC = Path(__file__).resolve().parents[1] / "mac"
NAMEN = ("MM_INGEST_URL", "FYOS_FINANZEN_URL", "FYOS_FINANZEN_AUTH_HEADER", "FYOS_FINANZEN_DB_URL", "FYOS_AUDIT_DB_URL",
         "FYOS_FINANZEN_RECLASSIFY_MODUL")


def _laden(monkeypatch, tmp_path, datei, **env):
    for n in NAMEN:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("MM_BRIDGE_LOG", str(tmp_path / "bridge.log"))
    monkeypatch.setenv("MM_BRIDGE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MM_WATCH_DIR", str(tmp_path / "watch"))
    for n, w in env.items():
        monkeypatch.setenv(n, w)
    spec = importlib.util.spec_from_file_location(f"mac_{datei}_unter_test", MAC / f"{datei}.py")
    m = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, m)  # Dataclasses schlagen ihr Modul dort nach
    spec.loader.exec_module(m)
    return m


def test_csv_watch_ohne_adresse_ist_nicht_eingerichtet_und_sendet_nichts(monkeypatch, tmp_path):
    m = _laden(monkeypatch, tmp_path, "moneymoney_csv_watch")
    monkeypatch.setattr(m, "_post_csv", lambda p: pytest.fail("ohne Adresse darf nichts gesendet werden"))
    monkeypatch.setattr(sys, "argv", ["moneymoney_csv_watch.py", "--once"])
    assert m.main() == 1
    assert "nicht eingerichtet" in (tmp_path / "bridge.log").read_text()


def test_csv_watch_kopfzeile_hat_keine_vorgabe_sondern_kommt_aus_der_umgebung(monkeypatch, tmp_path):
    assert _laden(monkeypatch, tmp_path, "moneymoney_csv_watch")._auth_kopf() == {}
    m = _laden(monkeypatch, tmp_path, "moneymoney_csv_watch", FYOS_FINANZEN_AUTH_HEADER="X-Test: wert")
    assert m._auth_kopf() == {"X-Test": "wert"}
    assert _laden(monkeypatch, tmp_path, "moneymoney_csv_watch", FYOS_FINANZEN_AUTH_HEADER="ohne-doppelpunkt")._auth_kopf() == {}


def test_csv_watch_url_aus_finanzen_url(monkeypatch, tmp_path):
    m = _laden(monkeypatch, tmp_path, "moneymoney_csv_watch", FYOS_FINANZEN_URL="https://finanzen.example.org/")
    assert m.INGEST_URL == "https://finanzen.example.org/api/finance/moneymoney/ingest"


def test_import_ohne_modul_ist_reclassify_nicht_eingerichtet(monkeypatch, tmp_path):
    m = _laden(monkeypatch, tmp_path, "moneymoney_import")
    assert m.trigger_reclassify() is False
    nichts = _laden(monkeypatch, tmp_path, "moneymoney_import", FYOS_FINANZEN_RECLASSIFY_MODUL="gibt_es_nicht_xyz")
    assert nichts.trigger_reclassify() is False


def test_import_db_adresse_alter_name_ist_alias_neuer_gewinnt(monkeypatch, tmp_path):
    m = _laden(monkeypatch, tmp_path, "moneymoney_import")
    benutzt = []

    class Psycopg:
        @staticmethod
        def connect(url):
            benutzt.append(url)
            return object()

    monkeypatch.setitem(sys.modules, "psycopg", Psycopg)
    with pytest.raises(SystemExit):
        m.connect_db()  # keine Vorgabe: ohne Adresse bricht der Import mit Hinweis ab
    monkeypatch.setenv("FYOS_AUDIT_DB_URL", "postgres://alt.example.org/db")
    m.connect_db()
    monkeypatch.setenv("FYOS_FINANZEN_DB_URL", "postgres://neu.example.org/db")
    m.connect_db()
    assert benutzt == ["postgres://alt.example.org/db", "postgres://neu.example.org/db"]
