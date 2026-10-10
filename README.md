English: [README.en.md](README.en.md)

# fyos-plugin-finanzen

Hub-Plugin `finance` für FYOS: Monatsplan, Kontostände und Umsätze aus MoneyMoney. Das Paket enthält keine persönliche
Konfiguration und zeigt auf keinen fremden Rechner: Adressen und Zugänge setzt der Betreiber, was fehlt, ist „nicht
eingerichtet“ (siehe Konfiguration).

Werkzeuge: `finance.monat`, `finance.posten_bezahlt`, `finance.posten_aussetzen`, `finance.konten`,
`finance.umsaetze` (lesend außer den zwei `posten_*`; IBAN, Referenz und Notizen geben die Lesesichten nicht weiter).

## Installation ins Hub-Bild

Das Paket hängt von `fyos-hub` ab und meldet sich über den Entry-Point `mcp_hub.plugins` (`finance`) selbst an:

    pip install /pfad/zu/fyos-plugin-finanzen        # oder: pip install git+<repo-url>

Im Dockerfile des Hub-Bildes (nach der Hub-Installation) eine Zeile `RUN pip install ./fyos-plugin-finanzen`. Ohne
Eintrag in der Hub-Konfiguration lädt der Hub das Plugin per Entry-Point. Mit Eintrag (Konfiguration, `enabled: false`):

    plugins:
      - name: finance
        config:
          finanzen_url: https://finanzen.example.org
          konten_url: https://konten.example.org

## Konfiguration

Es gibt keine Vorgabe auf einen Dienst. Plugin-`config:` vor Umgebung:

| config | Umgebung | Bedeutung |
|---|---|---|
| `finanzen_url` | `FYOS_FINANZEN_URL` | Monatsplan-Dienst (`monat`, `posten_bezahlt`, `posten_aussetzen`) |
| `konten_url` | `FYOS_FINANZEN_KONTEN_URL` | Dienst mit den MoneyMoney-Daten (`konten`, `umsaetze`) |
| `auth_header` | `FYOS_FINANZEN_AUTH_HEADER` | optionale Kopfzeile `Name: Wert` für die Aufrufe an die Dienste (keine Vorgabe) |

Was nicht gesetzt ist (leer zählt als nicht gesetzt), gilt als **nicht eingerichtet**: Lesende Werkzeuge antworten mit
`zustand: "nicht eingerichtet"` und dem Weg zur Einrichtung (`einrichten`), schreibende mit `ok: false` und demselben
Hinweis, `health` meldet `status: "nicht eingerichtet"` (oder „teilweise eingerichtet“, wenn nur ein Dienst gesetzt ist).
Ungültige Argumente werden unabhängig davon abgewiesen. Es geht keine Anfrage ins Leere hinaus.

### Alte Namen

Die früheren Namen gelten weiter als Alias und werden gelesen, wenn der neue Name nicht gesetzt ist. Sind beide gesetzt,
gewinnt der neue Name.

| Neuer Name | Alter Name |
|---|---|
| `FYOS_FINANZEN_KONTEN_URL` | `FYOS_FINANZEN_LIFE_URL` |
| `konten_url` (config) | `life_url` |

Die Dienste gehören nicht zu diesem Paket; siehe `dienst/README.md` für die Wege, die sie anbieten müssen.

## Mac-Seite (`mac/`)

MoneyMoney läuft nur auf dem Mac. Von dort gehen Umsätze und Kontostände zweimal täglich an den Webhook.

| Datei | Zweck |
|---|---|
| `moneymoney_export.sh` | Umsätze (`export transactions`) → `POST <Basis>/api/life/webhook/moneymoney`, danach Auto-Match und `moneymoney_konten.py` |
| `moneymoney_konten.py` | Kontostände (`export accounts`) → `…/moneymoney-konten`; von der IBAN nur die letzten vier Stellen |
| `moneymoney_csv_watch.py` | CSV-Beobachter → `FYOS_FINANZEN_URL` (oder `MM_INGEST_URL`); optional `FYOS_FINANZEN_AUTH_HEADER`; ohne Adresse „nicht eingerichtet“ |
| `moneymoney_import.py` | direkter Import in eine Postgres-Datenbank (`FYOS_FINANZEN_DB_URL`, alt `FYOS_AUDIT_DB_URL`); `--reclassify` nur mit `FYOS_FINANZEN_RECLASSIFY_MODUL` |
| `moneymoney_export.lua`, `moneymoney_export.scpt` | älterer Weg (Extension, AppleScript-Trigger) |
| `moneymoney-push.applescript` | schlanker Push aus MoneyMoney heraus |
| `*.plist`, `install.sh` | LaunchAgents; `install.sh` ersetzt `__MAC_DIR__` durch den Ordner und legt sie in `~/Library/LaunchAgents` ab |

Einrichtung:

1. `mkdir -p ~/.config/fyos`; darin `finance_base_url` (eine Zeile, z. B. `https://fyos.example.org`) und `finance_token`
   (Bearer-Token des Webhooks; Rechte 600, nie ins Repo). Alternativ `FYOS_BASIS_URL`, `FYOS_FINANCE_TOKEN_FILE`.
2. MoneyMoney entsperrt lassen (Skripte lesen nur; keine Überweisungen).
3. `mac/install.sh` und `launchctl load -w ~/Library/LaunchAgents/de.fyos.moneymoney-sync.plist`.
4. Test: `bash mac/moneymoney_export.sh 7`.

## Tests

    uv pip install -e ".[test]"      # fyos-hub muss installiert sein
    python3 -m pytest -q

`tests/test_plugin.py` prüft Lese- und Schreibwege (Netz ist eine Attrappe, Adressen sind Dokumentationsadressen auf
`example.org`), den Zustand „nicht eingerichtet“ je Werkzeug, Alias und Vorrang des neuen Namens. `tests/test_paket.py`
stellt sicher, dass im Paket keine LAN-Adresse und kein Dienst im Cluster vorkommt. `tests/test_mac_konten.py` prüft die
Mac-Seite.

GitHub Actions (`.github/workflows/tests.yml`) führt dieselben Tests gegen den Hub aus dem Quellbaum von
`fyos-os` aus, dazu `tools/sauber.py` (keine privaten Adressen, Konten oder
Geheimnisse im Baum und in der Historie) und gitleaks.

## Lizenz

Apache License, Version 2.0 (SPDX-Kennung `Apache-2.0`). Copyright 2026 sirfyyn and FYOS CONTRIBUTORS <!-- sauber: ok -->

Der vollständige Lizenztext steht in [`LICENSE`](LICENSE), der Urheberhinweis in [`NOTICE`](NOTICE).
