# dienst/ — Datenquelle (gehört nicht zu diesem Paket)

Das Plugin liest zwei Dienste, die hier bewusst **nicht** mitgeliefert werden. Wer das Plugin nutzen will, richtet sie
ein und nennt ihre Adressen (`FYOS_FINANZEN_URL`, `FYOS_FINANZEN_KONTEN_URL`, siehe README); ohne sie ist das Plugin
„nicht eingerichtet“.

| Dienst | Rolle | Wege, die das Plugin nutzt |
|---|---|---|
| Monatsplan-Dienst | Monatsplan (Posten, Einnahmen, Restschuld), Auto-Match gegen Umsätze | `GET /api/finance/month`, `PATCH /api/finance/month/{id}` |
| Konten-Dienst | MoneyMoney-Eingang: Umsätze und Kontostände | `POST /api/life/webhook/moneymoney`, `POST …/moneymoney-konten`, `GET …/finance-summary`, `GET …/finance-konten` |

Der Dienst, den der Betreiber dieses Pakets einsetzt, liegt in dessen eigenem Repo. Ein eigenständiger Dienst
`fyos-finanzen` mit genau diesen Wegen (Monatsplan + MoneyMoney-Eingang) ist die geplante Nachfolge; das Plugin spricht ihn
über dieselben beiden Einstellungen an.

Der Serverseiten-Test des Konten-Dienstes (nur erlaubte Felder werden gespeichert, neueste Aufnahme wird gelesen) gehört
zu diesem Dienst; das Gegenstück auf dem Mac prüft `tests/test_mac_konten.py`.
