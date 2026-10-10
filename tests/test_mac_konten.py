"""mac/moneymoney_konten.py: nur erlaubte Felder verlassen den Mac (ersetzt den Serverseiten-Test ohne Monorepo)."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

PFAD = Path(__file__).resolve().parents[1] / "mac" / "moneymoney_konten.py"


def _laden(monkeypatch):
    monkeypatch.setenv("FYOS_BASIS_URL", "https://fyos.example.org/")
    spec = importlib.util.spec_from_file_location("mm_konten_unter_test", PFAD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_url_aus_basis(monkeypatch):
    monkeypatch.delenv("MM_KONTEN_URL", raising=False)
    m = _laden(monkeypatch)
    assert m.URL == "https://fyos.example.org/api/life/webhook/moneymoney-konten"


def test_nur_erlaubte_felder_und_iban_ende(monkeypatch):
    m = _laden(monkeypatch)
    roh = [{
        "uuid": "u1", "name": "Giro", "type": "Girokonto", "bankCode": "12345678",
        "accountNumber": "DE00 1234 1234", "owner": "Vorname Nachname",
        "balance": [[12.5, "EUR"]], "refreshTimestamp": datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc),
    }, {
        "uuid": "g", "name": "Alle", "group": True, "indentation": 1,
        "refreshTimestamp": datetime(1970, 1, 1, tzinfo=timezone.utc),
    }]
    konten = m.konten_aus(roh)
    assert konten[0]["iban_ende"] == "1234" and konten[0]["saldo"] == 12.5
    assert "owner" not in konten[0] and "accountNumber" not in konten[0]
    assert konten[0]["aktualisiert"].startswith("2026-10-05T14:00")
    assert konten[1]["gruppe"] is True and konten[1]["aktualisiert"] is None and konten[1]["ebene"] == 1
