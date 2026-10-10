#!/usr/bin/python3
"""MoneyMoney → FYOS: Kontostände (05.10.2026). Läuft nach moneymoney_export.sh (gleicher Token, gleicher Agent).

Liest `export accounts` per AppleScript (nur lesen; MoneyMoney muss laufen und entsperrt sein) und schickt eine
Momentaufnahme an den Konten-Dienst (/api/life/webhook/moneymoney-konten). Gesendet werden Name, Art, Bank, Saldo,
Währung, Stand der Bank-Aktualisierung und die UUID (ordnet Umsätze Konten zu); von der IBAN nur die letzten
vier Stellen, kein Inhaber. Der Hub liest das über finance.konten.

System-Python (/usr/bin/python3) wie moneymoney_export.sh: Homebrew-Python hat dort kaputtes pyexpat.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timezone

def _basis() -> str:
    b = os.getenv("FYOS_BASIS_URL", "").strip()
    if not b:
        try:
            b = open(os.path.expanduser("~/.config/fyos/finance_base_url")).read().strip()
        except OSError:
            b = ""
    return b.rstrip("/")


URL = os.getenv("MM_KONTEN_URL") or (_basis() + "/api/life/webhook/moneymoney-konten")
TOKEN_DATEI = os.getenv(
    "FYOS_FINANCE_TOKEN_FILE", os.path.expanduser("~/.config/fyos/finance_token")
)


def _iso(v):
    if isinstance(v, (date, datetime)) and v.year < 2000:
        return None  # Kontengruppen: refreshTimestamp = 1970
    if isinstance(v, datetime):
        return v.replace(tzinfo=v.tzinfo or timezone.utc).isoformat(timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    return str(v) if v else None


def _saldo(k):
    # MoneyMoney: balance = [[Betrag, Währung], …] (je Währung ein Eintrag)
    b = k.get("balance") or []
    if b and isinstance(b[0], (list, tuple)) and b[0]:
        return round(float(b[0][0]), 2), str(
            b[0][1] if len(b[0]) > 1 else k.get("currency") or "EUR"
        )
    return None, k.get("currency") or "EUR"


def konten_aus(roh):
    aus = []
    for k in roh or []:
        saldo, waehrung = _saldo(k)
        nummer = str(k.get("accountNumber") or "").replace(" ", "")
        aus.append(
            {
                "uuid": k.get("uuid"),
                "name": k.get("name") or "",
                "typ": k.get("type"),
                "bank": k.get("bankCode") or None,
                "iban_ende": nummer[-4:] if len(nummer) >= 4 else None,
                "saldo": saldo,
                "waehrung": waehrung,
                "aktualisiert": _iso(k.get("refreshTimestamp")),
                "gruppe": bool(k.get("group")),
                "ebene": int(k.get("indentation") or 0),
            }
        )
    return aus


def main():
    r = subprocess.run(
        ["osascript", "-e", 'tell application "MoneyMoney" to export accounts'],
        capture_output=True,
        timeout=120,
    )
    if r.returncode != 0:
        print(
            "ERROR: export accounts fehlgeschlagen (MoneyMoney gesperrt oder zu?):",
            r.stderr.decode()[:200],
        )
        return 1
    konten = konten_aus(plistlib.loads(r.stdout))
    try:
        token = open(TOKEN_DATEI).read().strip()
    except OSError:
        print("ERROR: kein Finance-Token", TOKEN_DATEI)
        return 1
    koerper = json.dumps(
        {"konten": konten, "stand": datetime.now(timezone.utc).isoformat(timespec="minutes")}
    )
    req = urllib.request.Request(
        URL,
        data=koerper.encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as a:
        print("Konten:", len(konten), a.read().decode()[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
