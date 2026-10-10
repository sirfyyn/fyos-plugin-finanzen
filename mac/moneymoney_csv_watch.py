#!/usr/bin/env python3
"""Iter-1223 — MoneyMoney CSV-Watch (Mac-side LaunchAgent).

Beobachtet ~/Documents/MoneyMoney-Export/ (oder MM_WATCH_DIR aus env).
Bei neuer/geaenderter CSV: POST an den Monatsplan-Dienst
  POST $MM_INGEST_URL  (Vorgabe: $FYOS_FINANZEN_URL/api/finance/moneymoney/ingest)
mit Original-Bytes als Body. Gemerkt wird die Datei via
  ~/Library/Application Support/fyos-moneymoney-bridge/state.json
(filename → mtime+size), so dass nur echte Aenderungen gepostet werden.

Modes:
  --once     Single-Pass (LaunchAgent default — alle 5 Minuten)
  --daemon   Loop alle WATCH_INTERVAL_S (default 60s) — fuer Foreground-Tests
  --reset    Loescht state.json (re-ingest aller Files beim naechsten Run)

Auth: optionale Kopfzeile "Name: Wert" aus FYOS_FINANZEN_AUTH_HEADER (wie im Hub-Plugin; es gibt keine Vorgabe). Von
extern: Session-Cookie des Anmeldedienstes via env FYOS_INGEST_COOKIE (nie ins Repo).
Ohne Adresse (FYOS_FINANZEN_URL oder MM_INGEST_URL) ist die Bridge nicht eingerichtet: sie sagt das und beendet sich mit 1.

Exit codes:
  0 = success (auch wenn 0 Files neu)
  1 = config error
  2 = network error (alle Posts fehlgeschlagen)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import urllib.error
import urllib.parse
import urllib.request


HOME = Path.home()
WATCH_DIR = Path(os.getenv("MM_WATCH_DIR", str(HOME / "Documents" / "MoneyMoney-Export")))
INGEST_URL = os.getenv("MM_INGEST_URL") or (
    os.getenv("FYOS_FINANZEN_URL", "").rstrip("/") + "/api/finance/moneymoney/ingest"
)
STATE_DIR = Path(os.getenv(
    "MM_BRIDGE_STATE_DIR",
    str(HOME / "Library" / "Application Support" / "fyos-moneymoney-bridge"),
))
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = Path(os.getenv("MM_BRIDGE_LOG", "/tmp/fyos-moneymoney-bridge.log"))
INGEST_COOKIE = os.getenv("FYOS_INGEST_COOKIE", "")
AUTH_HEADER = os.getenv("FYOS_FINANZEN_AUTH_HEADER", "")  # optional, "Name: Wert"; keine Vorgabe
WATCH_INTERVAL_S = int(os.getenv("MM_WATCH_INTERVAL_S", "60"))


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} {msg}\n"
    sys.stderr.write(line)
    try:
        with LOG_FILE.open("a") as f:
            f.write(line)
    except OSError:
        pass


def _auth_kopf() -> dict[str, str]:
    """Die Kopfzeile aus FYOS_FINANZEN_AUTH_HEADER ("Name: Wert"); ohne Einstellung keine."""
    name, doppelpunkt, wert = AUTH_HEADER.partition(":")
    return {name.strip(): wert.strip()} if doppelpunkt and name.strip() else {}


def _load_state() -> dict[str, dict[str, Any]]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, dict[str, Any]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_FILE)


def _file_signature(p: Path) -> dict[str, Any]:
    st = p.stat()
    return {"mtime": int(st.st_mtime), "size": st.st_size}


def _post_csv(path: Path) -> tuple[bool, str]:
    """POST CSV-Bytes an ingest. Return (ok, message)."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return False, f"read failed: {exc}"

    url = f"{INGEST_URL}?filename={urllib.parse.quote(path.name)}"
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "User-Agent": "fyos-moneymoney-bridge/1.0",
            **_auth_kopf(),
            **({"Cookie": INGEST_COOKIE} if INGEST_COOKIE else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, f"HTTP {resp.status} {body[:200]}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} {exc.read()[:200].decode('utf-8', errors='replace')}"
    except urllib.error.URLError as exc:
        return False, f"URLError {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, f"unexpected {exc!r}"


def scan_and_post() -> tuple[int, int, int]:
    """Pass: returns (posted, skipped_unchanged, errors)."""
    if not WATCH_DIR.exists():
        _log(f"WATCH_DIR not found, creating: {WATCH_DIR}")
        try:
            WATCH_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _log(f"mkdir failed: {exc}")
            return 0, 0, 0

    state = _load_state()
    posted = 0
    skipped = 0
    errors = 0

    for p in sorted(WATCH_DIR.glob("*.csv")):
        try:
            sig = _file_signature(p)
        except OSError as exc:
            _log(f"stat failed {p}: {exc}")
            errors += 1
            continue

        prev = state.get(p.name)
        if prev and prev.get("mtime") == sig["mtime"] and prev.get("size") == sig["size"]:
            skipped += 1
            continue

        ok, msg = _post_csv(p)
        if ok:
            state[p.name] = {**sig, "last_post_at": datetime.now(timezone.utc).isoformat()}
            posted += 1
            _log(f"POSTED {p.name} ({sig['size']} bytes) → {msg}")
        else:
            errors += 1
            _log(f"FAIL  {p.name}: {msg}")

    _save_state(state)
    return posted, skipped, errors


def main() -> int:
    ap = argparse.ArgumentParser(description="MoneyMoney CSV-Watch → Ingest des Monatsplan-Dienstes")
    ap.add_argument("--once", action="store_true", help="Single-Pass (LaunchAgent default)")
    ap.add_argument("--daemon", action="store_true", help="Loop forever (Foreground)")
    ap.add_argument("--reset", action="store_true", help="Loesche state.json (re-ingest alles)")
    args = ap.parse_args()

    if args.reset:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            _log("state.json reset")
        return 0

    if not INGEST_URL.startswith(("http://", "https://")):
        _log("nicht eingerichtet: FYOS_FINANZEN_URL (oder MM_INGEST_URL) nennt keinen Dienst — nichts gesendet")
        return 1

    if args.daemon:
        _log(f"daemon-mode start (interval={WATCH_INTERVAL_S}s, dir={WATCH_DIR})")
        any_posted = False
        try:
            while True:
                p, s, e = scan_and_post()
                if p:
                    any_posted = True
                _log(f"pass: posted={p} skipped={s} errors={e}")
                time.sleep(WATCH_INTERVAL_S)
        except KeyboardInterrupt:
            _log("daemon stopped via SIGINT")
            return 0 if any_posted or True else 0

    # Default = once
    p, s, e = scan_and_post()
    _log(f"once: posted={p} skipped={s} errors={e}")
    if e and not p:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
