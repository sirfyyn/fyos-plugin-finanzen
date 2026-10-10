#!/usr/bin/env python3
"""
BL-MONEYMONEY-PIPELINE-001 — MoneyMoney → FYOS Finance-Store Import.

CLI-Adapter der JSON-Exporte aus MoneyMoney (Mac, via FYOSExport.lua +
moneymoney_export.scpt) in die Postgres-Tabellen des
`finance`-Schemas (Datenbank des Betreibers, FYOS_FINANZEN_DB_URL) normalisiert und idempotent upsertet.

Workflow:
    1. JSON lesen (Datei oder stdin)
    2. Schema-Version validieren
    3. Je Account: upsert finance.accounts (stable account_ref = MM-uuid)
    4. Je Transaktion: upsert finance.transactions
       (stable source_ref = MM-transaction-id, source_type = "moneymoney")
    5. Nach Import: optional die Neu-Kategorisierung des Betreibers anstossen
       (ueber --reclassify Flag, default: off; das Modul nennt FYOS_FINANZEN_RECLASSIFY_MODUL)

Idempotent:
    - Accounts: UPSERT via (provider='moneymoney', account_ref=mm_uuid)
    - Transactions: UPSERT via (provider='moneymoney', source_ref=mm_tx_id)
    - Re-run mit gleichem JSON = 0 neue Rows, nur updated_at wird aktualisiert

Usage:
    python3 mac/moneymoney_import.py --json-file /tmp/mm.json
    cat /tmp/mm.json | python3 mac/moneymoney_import.py --stdin
    python3 mac/moneymoney_import.py --json-file mm.json --dry-run
    python3 mac/moneymoney_import.py --json-file mm.json --reclassify

Env:
    FYOS_FINANZEN_DB_URL            — postgres://db.example.org:5432/fyos_finanzen (mit Zugangsdaten nach libpq-Art)
                                      (alter Name FYOS_AUDIT_DB_URL gilt als Alias, der neue Name gewinnt;
                                      keine Vorgabe: ohne Adresse ist der Import nicht eingerichtet)
    FYOS_FINANZEN_RECLASSIFY_MODUL  — optional: Python-Modul mit reclassify_all() fuer --reclassify
                                      (ohne Angabe ist --reclassify nicht eingerichtet und tut nichts)
    LOG_LEVEL                       — default INFO

Exit codes:
    0 = success (auch wenn 0 rows upserted)
    1 = config error (missing DB_URL, bad JSON)
    2 = schema mismatch / unsupported version
    3 = DB-error
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] mm-import: %(message)s",
)
log = logging.getLogger("moneymoney_import")

SUPPORTED_SCHEMA_VERSIONS = {"1.0"}
PROVIDER = "moneymoney"
SOURCE_TYPE = "moneymoney"

# Kategorie-Rules (bewusst im Importer, damit er ohne den Dienst des Betreibers laufen kann;
# eine spaetere Neu-Kategorisierung des Dienstes ueberschreibt sie).
_CATEGORY_RULES = [
    (r"(rewe|aldi|lidl|edeka|kaufland|netto|penny)", "Lebensmittel"),
    (r"(amazon|ebay|zalando|otto)", "Online-Shopping"),
    (r"(paypal)", "PayPal"),
    (r"(netflix|spotify|apple|disney)", "Streaming/Abo"),
    (r"(strom|gas|wasser|vattenfall|stadtwerk)", "Energie"),
    (r"(miete|warmmiete|kaltmiete)", "Miete"),
    (r"(tankstelle|shell|aral|bp|esso)", "Tanken"),
    (r"(restaurant|cafe|mcdonalds|burger|lieferando)", "Restaurant"),
    (r"(gehalt|lohn|entgelt)", "Einkommen"),
]


# ── Normalization helpers ─────────────────────────────────────────────────────

def _mask_iban(iban: str | None) -> str | None:
    if not iban:
        return None
    iban = iban.strip()
    if len(iban) < 8:
        return iban
    return iban[:4] + "*" * (len(iban) - 8) + iban[-4:]


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            log.warning("Unparseable date: %r", value)
            return None
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _categorize(mm_category: str | None, purpose: str, counterparty: str) -> str:
    """MoneyMoney-Kategorie gewinnt, sonst Regex-Fallback."""
    if mm_category and mm_category.strip():
        return mm_category.strip()
    text = f"{purpose} {counterparty}".lower()
    for pattern, category in _CATEGORY_RULES:
        if re.search(pattern, text):
            return category
    return "Sonstiges"


def _direction(amount: Decimal) -> str:
    return "credit" if amount >= 0 else "debit"


# ── Models ────────────────────────────────────────────────────────────────────

@dataclass
class NormalizedAccount:
    mm_uuid: str
    name: str
    owner: str | None
    iban_masked: str | None
    currency: str
    balance: Decimal | None
    balance_date: datetime | None
    account_type: str
    is_loan: bool


@dataclass
class NormalizedTx:
    mm_tx_id: str
    account_mm_uuid: str
    amount: Decimal
    currency: str
    direction: str
    category: str
    description: str
    counterparty: str
    transaction_date: date
    booked_at: datetime | None
    raw: dict[str, Any]


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_export(payload: dict[str, Any]) -> tuple[list[NormalizedAccount], list[NormalizedTx]]:
    """Parse MoneyMoney export JSON → (accounts, transactions).

    Raises:
        ValueError: on schema-version mismatch or missing fields.
    """
    version = payload.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"Unsupported schema_version {version!r}, expected one of {SUPPORTED_SCHEMA_VERSIONS}"
        )

    accounts_out: list[NormalizedAccount] = []
    txs_out: list[NormalizedTx] = []

    for acc in payload.get("accounts", []):
        mm_uuid = acc.get("account_id") or acc.get("uuid")
        if not mm_uuid:
            log.warning("Skipping account without account_id: %r", acc.get("name"))
            continue

        balance = acc.get("balance")
        balance_dec = Decimal(str(balance)) if balance is not None else None

        accounts_out.append(
            NormalizedAccount(
                mm_uuid=str(mm_uuid),
                name=str(acc.get("name") or ""),
                owner=acc.get("owner"),
                iban_masked=_mask_iban(acc.get("account_number")),
                currency=str(acc.get("currency") or "EUR"),
                balance=balance_dec,
                balance_date=_parse_datetime(acc.get("balance_date")),
                account_type=str(acc.get("account_type") or "other"),
                is_loan=bool(acc.get("is_loan", False)),
            )
        )

        for tx in acc.get("transactions", []):
            tx_id = tx.get("id")
            if not tx_id:
                # MoneyMoney liefert id sehr zuverlaessig; ohne id kein dedup-key → skip
                log.warning("Skipping tx without id in account %s", mm_uuid)
                continue

            amount_raw = tx.get("amount")
            if amount_raw is None:
                continue
            amount = Decimal(str(amount_raw))

            booking_date = _parse_date(tx.get("booking_date")) or _parse_date(tx.get("value_date"))
            if booking_date is None:
                log.warning("Skipping tx %s without date", tx_id)
                continue

            purpose = str(tx.get("purpose") or "")
            name = str(tx.get("name") or "")
            category = _categorize(tx.get("category"), purpose, name)

            # description: purpose gewinnt, fallback auf booking_text
            description = purpose or str(tx.get("booking_text") or "")

            txs_out.append(
                NormalizedTx(
                    mm_tx_id=str(tx_id),
                    account_mm_uuid=str(mm_uuid),
                    amount=amount,
                    currency=str(tx.get("currency") or "EUR"),
                    direction=_direction(amount),
                    category=category,
                    description=description[:500],
                    counterparty=name[:200],
                    transaction_date=booking_date,
                    booked_at=_parse_datetime(tx.get("booking_date")),
                    raw=tx,
                )
            )

    return accounts_out, txs_out


# ── Deduplication ─────────────────────────────────────────────────────────────

def dedupe_transactions(txs: list[NormalizedTx]) -> list[NormalizedTx]:
    """Within a single export file: drop duplicate mm_tx_id (keep first).

    Cross-run dedup passiert in der DB via UNIQUE(provider, source_ref).
    """
    seen: set[tuple[str, str]] = set()
    result: list[NormalizedTx] = []
    for tx in txs:
        key = (tx.account_mm_uuid, tx.mm_tx_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(tx)
    return result


# ── DB Upsert (psycopg3) ──────────────────────────────────────────────────────

_UPSERT_ACCOUNT_SQL = """
INSERT INTO finance.accounts
    (provider, account_name, account_ref, iban_masked, currency, is_active, updated_at)
VALUES (%(provider)s, %(name)s, %(ref)s, %(iban)s, %(currency)s, TRUE, NOW())
ON CONFLICT (provider, account_ref)
DO UPDATE SET
    account_name = EXCLUDED.account_name,
    iban_masked  = EXCLUDED.iban_masked,
    currency     = EXCLUDED.currency,
    updated_at   = NOW()
RETURNING id;
"""

# Hinweis: finance.accounts hat aktuell keinen UNIQUE-Constraint auf
# (provider, account_ref). Migration wird in docs/services/finance-pipeline.md
# als Folge-Item dokumentiert. Interimsweise nutzen wir _select_or_insert_account.

_SELECT_ACCOUNT_SQL = """
SELECT id FROM finance.accounts
WHERE provider = %(provider)s AND account_ref = %(ref)s
LIMIT 1;
"""

_INSERT_ACCOUNT_SQL = """
INSERT INTO finance.accounts
    (provider, account_name, account_ref, iban_masked, currency, is_active, updated_at)
VALUES (%(provider)s, %(name)s, %(ref)s, %(iban)s, %(currency)s, TRUE, NOW())
RETURNING id;
"""

_UPDATE_ACCOUNT_SQL = """
UPDATE finance.accounts
SET account_name=%(name)s, iban_masked=%(iban)s, currency=%(currency)s, updated_at=NOW()
WHERE id=%(id)s;
"""

_SELECT_TX_SQL = """
SELECT id FROM finance.transactions
WHERE provider = %(provider)s AND source_ref = %(ref)s
LIMIT 1;
"""

_INSERT_TX_SQL = """
INSERT INTO finance.transactions
    (account_id, provider, source_type, source_ref, amount, currency, direction,
     category, description, counterparty, transaction_date, booked_at, raw_data)
VALUES (%(account_id)s, %(provider)s, %(source_type)s, %(source_ref)s,
        %(amount)s, %(currency)s, %(direction)s, %(category)s, %(description)s,
        %(counterparty)s, %(transaction_date)s, %(booked_at)s, %(raw)s::jsonb);
"""

_UPDATE_TX_SQL = """
UPDATE finance.transactions
SET amount=%(amount)s, currency=%(currency)s, direction=%(direction)s,
    category=%(category)s, description=%(description)s, counterparty=%(counterparty)s,
    transaction_date=%(transaction_date)s, booked_at=%(booked_at)s, raw_data=%(raw)s::jsonb
WHERE id=%(id)s;
"""


def upsert_all(
    conn: Any,
    accounts: list[NormalizedAccount],
    txs: list[NormalizedTx],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Upsert accounts + transactions. Returns counters.

    `conn` ist eine psycopg3-Connection (oder Mock mit .cursor()).
    Separation von parse_export ermoeglicht Tests ohne DB.
    """
    stats = {
        "accounts_inserted": 0,
        "accounts_updated": 0,
        "tx_inserted": 0,
        "tx_updated": 0,
        "tx_skipped_no_account": 0,
    }

    account_id_map: dict[str, Any] = {}

    with conn.cursor() as cur:
        for acc in accounts:
            params = {
                "provider": PROVIDER,
                "ref": acc.mm_uuid,
                "name": acc.name,
                "iban": acc.iban_masked,
                "currency": acc.currency,
            }
            cur.execute(_SELECT_ACCOUNT_SQL, params)
            row = cur.fetchone()
            if row:
                account_id = row[0]
                if not dry_run:
                    cur.execute(_UPDATE_ACCOUNT_SQL, {**params, "id": account_id})
                stats["accounts_updated"] += 1
            else:
                if dry_run:
                    account_id = f"<dry-run-{acc.mm_uuid}>"
                else:
                    cur.execute(_INSERT_ACCOUNT_SQL, params)
                    account_id = cur.fetchone()[0]
                stats["accounts_inserted"] += 1
            account_id_map[acc.mm_uuid] = account_id

        for tx in txs:
            account_id = account_id_map.get(tx.account_mm_uuid)
            if account_id is None:
                stats["tx_skipped_no_account"] += 1
                log.warning(
                    "Tx %s has account_mm_uuid=%s but no matching account in this export",
                    tx.mm_tx_id, tx.account_mm_uuid,
                )
                continue

            tx_sel = {"provider": PROVIDER, "ref": tx.mm_tx_id}
            cur.execute(_SELECT_TX_SQL, tx_sel)
            row = cur.fetchone()

            tx_params = {
                "account_id": account_id,
                "provider": PROVIDER,
                "source_type": SOURCE_TYPE,
                "source_ref": tx.mm_tx_id,
                "amount": tx.amount,
                "currency": tx.currency,
                "direction": tx.direction,
                "category": tx.category,
                "description": tx.description,
                "counterparty": tx.counterparty,
                "transaction_date": tx.transaction_date,
                "booked_at": tx.booked_at,
                "raw": json.dumps(tx.raw, default=str),
            }

            if row:
                if not dry_run:
                    cur.execute(_UPDATE_TX_SQL, {**tx_params, "id": row[0]})
                stats["tx_updated"] += 1
            else:
                if not dry_run:
                    cur.execute(_INSERT_TX_SQL, tx_params)
                stats["tx_inserted"] += 1

    if not dry_run:
        conn.commit()
    return stats


# ── Reclassify-Hook ───────────────────────────────────────────────────────────

def trigger_reclassify() -> bool:
    """Ruft reclassify_all() des Moduls aus FYOS_FINANZEN_RECLASSIFY_MODUL. Soft-fail; ohne Modul nicht eingerichtet."""
    modul = os.getenv("FYOS_FINANZEN_RECLASSIFY_MODUL", "").strip()
    if not modul:
        log.info("--reclassify: nicht eingerichtet (FYOS_FINANZEN_RECLASSIFY_MODUL nennt kein Modul) — uebersprungen")
        return False
    try:
        # Lazy import — der Importer koppelt nicht hart an das Modul des Betreibers
        mod = importlib.import_module(modul)
        if hasattr(mod, "reclassify_all"):
            mod.reclassify_all()
            log.info("Triggered %s.reclassify_all()", modul)
            return True
        log.info("%s has no reclassify_all() — skipping", modul)
        return False
    except ImportError:
        log.info("%s not importable — reclassify skipped", modul)
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("reclassify trigger failed: %s", exc)
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

def load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.stdin:
        raw = sys.stdin.read()
    elif args.json_file:
        raw = Path(args.json_file).read_text(encoding="utf-8")
    else:
        raise ValueError("Either --json-file or --stdin required")
    return json.loads(raw)


def connect_db() -> Any:
    """Open psycopg3 connection. Kept out of parse_export for testability."""
    db_url = (os.getenv("FYOS_FINANZEN_DB_URL") or os.getenv("FYOS_AUDIT_DB_URL") or "").strip()  # alter Name = Alias
    if not db_url:
        log.error("nicht eingerichtet: FYOS_FINANZEN_DB_URL not set (alter Name: FYOS_AUDIT_DB_URL)")
        sys.exit(1)
    try:
        import psycopg  # type: ignore
    except ImportError:
        log.error("psycopg (v3) not installed. pip install psycopg[binary]")
        sys.exit(1)
    return psycopg.connect(db_url)


def main() -> int:
    ap = argparse.ArgumentParser(description="Import MoneyMoney export → FYOS finance-schema")
    ap.add_argument("--json-file", help="Path to MoneyMoney export JSON")
    ap.add_argument("--stdin", action="store_true", help="Read JSON from stdin")
    ap.add_argument("--dry-run", action="store_true", help="Parse + normalize, no DB writes")
    ap.add_argument("--reclassify", action="store_true", help="Trigger the reclassify hook (FYOS_FINANZEN_RECLASSIFY_MODUL) after import")
    args = ap.parse_args()

    try:
        payload = load_payload(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.error("Failed to read JSON: %s", exc)
        return 1

    try:
        accounts, txs = parse_export(payload)
    except ValueError as exc:
        log.error("Parse failed: %s", exc)
        return 2

    txs = dedupe_transactions(txs)
    log.info("Parsed %d accounts, %d transactions (after dedupe)", len(accounts), len(txs))

    if args.dry_run:
        log.info("DRY-RUN — skipping DB writes")
        for acc in accounts:
            log.info("  account %s (%s) balance=%s", acc.name, acc.account_type, acc.balance)
        log.info("Summary: would upsert %d accounts + %d transactions", len(accounts), len(txs))
        return 0

    try:
        conn = connect_db()
        try:
            stats = upsert_all(conn, accounts, txs, dry_run=False)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log.error("DB operation failed: %s", exc)
        return 3

    log.info("Done: %s", stats)

    if args.reclassify:
        trigger_reclassify()

    return 0


if __name__ == "__main__":
    sys.exit(main())
