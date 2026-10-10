#!/bin/bash
# MoneyMoney → FYOS Finance Pipeline
# Exportiert Transaktionen via AppleScript und sendet sie an den Konten-Dienst (Webhook)
#
# Usage: ./moneymoney_export.sh [days]
# Default: letzte 30 Tage
#
# Voraussetzung: MoneyMoney laeuft, die Konten sind eingerichtet
# Ziel: POST <FYOS_BASIS_URL>/api/life/webhook/moneymoney (Store, den die Finanz-Sichten lesen). JSON + Bearer.
#
# Konfiguration (Umgebung oder Datei ~/.config/fyos/finance_base_url mit einer Zeile):
#   FYOS_BASIS_URL     Adresse der eigenen FYOS-Installation, z. B. https://fyos.example.org
#   MM_INGEST_URL      voller Weg zum Webhook (ueberschreibt FYOS_BASIS_URL)
#   FYOS_FINANCE_TOKEN_FILE / FYOS_FINANCE_TOKEN   Bearer-Token (Datei oder Wert; nie ins Repo)

DAYS=${1:-30}
BASIS_DATEI="${FYOS_FINANCE_BASE_URL_FILE:-$HOME/.config/fyos/finance_base_url}"
FYOS_BASIS_URL="${FYOS_BASIS_URL:-$(cat "$BASIS_DATEI" 2>/dev/null)}"
FYOS_BASIS_URL="${FYOS_BASIS_URL%/}"
INGEST_URL="${MM_INGEST_URL:-$FYOS_BASIS_URL/api/life/webhook/moneymoney}"
if [ -z "$FYOS_BASIS_URL" ] && [ -z "$MM_INGEST_URL" ]; then
    echo "ERROR: FYOS_BASIS_URL (oder $BASIS_DATEI) fehlt. Abbruch."
    exit 1
fi
export FYOS_BASIS_URL
TOKEN_FILE="${FYOS_FINANCE_TOKEN_FILE:-$HOME/.config/fyos/finance_token}"
FINANCE_TOKEN="${FYOS_FINANCE_TOKEN:-$(cat "$TOKEN_FILE" 2>/dev/null)}"
EXPORT_FILE="/tmp/moneymoney_export.json"

echo "MoneyMoney → FYOS Export (letzte $DAYS Tage)"

# 1. Transaktionen via AppleScript exportieren
FROM_DATE=$(date -v-${DAYS}d +%Y-%m-%d)
TO_DATE=$(date +%Y-%m-%d)

osascript << APPLEOF
tell application "MoneyMoney"
    set exportData to export transactions from date "$FROM_DATE" to date "$TO_DATE" as "plist"
    -- Schreibe in temporaere Datei
    set fileRef to open for access POSIX file "$EXPORT_FILE" with write permission
    set eof fileRef to 0
    write exportData to fileRef as «class utf8»
    close access fileRef
end tell
APPLEOF

if [ $? -ne 0 ]; then
    echo "ERROR: AppleScript Export fehlgeschlagen. Ist MoneyMoney offen?"
    exit 1
fi

echo "Export: $EXPORT_FILE"

# 2. plist → FYOS API transformieren
# MoneyMoney exportiert als Apple-plist (NICHT JSON) → plistlib statt json.load.
# /usr/bin/python3 (System) erzwungen: Homebrew-python3.14 hat kaputtes pyexpat.
/usr/bin/python3 << PYEOF
import json, sys, plistlib
from datetime import datetime, date

try:
    with open("$EXPORT_FILE", "rb") as f:
        raw = plistlib.load(f)
except Exception as e:
    print(f"ERROR: plist parse failed: {e}")
    sys.exit(1)

def isodate(v):
    if isinstance(v, (datetime, date)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    return str(v or "")

# Merchant→Kategorie (leichtgewichtig, damit Hero-Kategorie-Balken sinnvoll sind).
import re
_RULES = [
    (r"rewe|aldi|lidl|edeka|kaufland|netto|penny|denn", "Lebensmittel"),
    (r"amazon|ebay|zalando|otto|mediamarkt|saturn", "Shopping"),
    (r"paypal", "PayPal"),
    (r"netflix|spotify|apple|disney|prime|youtube", "Streaming/Abo"),
    (r"strom|gas|wasser|vattenfall|e\.?on|stadtwerk", "Energie"),
    (r"miete|warmmiete|kaltmiete|nebenkost", "Miete"),
    (r"tankstelle|shell|aral|\bbp\b|esso|total|jet|zollern.?automat", "Tanken"),
    (r"bahn|hvv|mvv|bvg|\bdb\b|fahrschein|flixbus", "Mobilität"),
    (r"apotheke|arzt|krankenk|gesundheit|dm |rossmann", "Gesundheit"),
    (r"restaurant|cafe|café|mcdonald|burger|pizza|lieferando|kebab|bäcker", "Gastronomie"),
    (r"versicherung|allianz|axa|huk|ergo", "Versicherung"),
    (r"kredit|darlehen|tilgung|finanzierung", "Kredit"),
    (r"gehalt|lohn|entgelt|landratsamt", "Einkommen"),
    (r"anthropic|openai|github|hetzner|cloud", "IT/AI"),
]
def categorize(name, amount):
    low = (name or "").lower()
    for pat, cat in _RULES:
        if re.search(pat, low):
            return cat
    return "Einkommen" if amount > 0 else "Sonstiges"

# MoneyMoney-plist (keys: id, amount[signed], currency, name, bookingDate, valueDate,
# bookingText, purpose?, accountUuid) → Webhook-Format /moneymoney des Konten-Dienstes.
transactions = []
for tx in raw.get("transactions", []) if isinstance(raw, dict) else (raw or []):
    amount = tx.get("amount", 0) or 0
    name = tx.get("name", "") or ""
    transactions.append({
        "id": str(tx.get("id") or f"mm-{isodate(tx.get('bookingDate'))}-{abs(amount)}"),
        "amount": round(float(amount), 2),          # SIGNED — Webhook nutzt Vorzeichen
        "currency": tx.get("currency", "EUR"),
        "name": name,
        "purpose": tx.get("purpose") or tx.get("bookingText") or name,
        "category": categorize(name, amount),
        "account": tx.get("accountUuid", ""),
        "bookingDate": isodate(tx.get("bookingDate") or tx.get("valueDate")),
    })

with open("$EXPORT_FILE.json", "w") as f:
    json.dump({"transactions": transactions}, f, ensure_ascii=False)

print(f"Transformiert: {len(transactions)} Transaktionen → JSON")
PYEOF

if [ $? -ne 0 ]; then
    echo "ERROR: Transformation fehlgeschlagen"
    exit 1
fi

# 3. An den /moneymoney-Webhook des Konten-Dienstes senden (JSON + Bearer).
# Das ist der Store, den die Mobile-/Web-Finance-Views lesen (life.pipeline_data →
# /api/life/webhook/finance-summary). Der Webhook-Pfad muss von aussen ohne Anmeldung erreichbar sein
# (er prueft den Bearer-Token selbst).
if [ -z "$FINANCE_TOKEN" ]; then
    echo "ERROR: kein Finance-Token ($TOKEN_FILE fehlt). Abbruch."
    exit 1
fi
echo "Sende an $INGEST_URL ..."
RESPONSE=$(curl -s -w " [HTTP %{http_code}]" -X POST "$INGEST_URL" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $FINANCE_TOKEN" \
    --data-binary @"$EXPORT_FILE.json" \
    --max-time 30 2>&1)

echo "Response: $RESPONSE"

# Nach dem Ingest direkt Auto-Match anstossen (geplante Posten werden
# automatisch auf bezahlt gesetzt; Betrag+Name+Datumsfenster ±10d).
MATCH=$(curl -s -X POST "${FYOS_BASIS_URL}/api/finance/expenses/auto-match" \
    -H "Authorization: Bearer $FINANCE_TOKEN" --max-time 20 2>&1)
echo "Auto-Match: $MATCH"

# 2026-10-05: Kontostände dazu (für den Hub, finance.konten). Fehler hier brechen den Umsatz-Export nicht ab.
/usr/bin/python3 "$(dirname "$0")/moneymoney_konten.py" || echo "WARN: Kontostände nicht gesendet"

# Cleanup
rm -f "$EXPORT_FILE" "$EXPORT_FILE.json"

echo "Done."
