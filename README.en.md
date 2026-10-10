Deutsch: [README.md](README.md)

# fyos-plugin-finanzen

Hub plugin `finance` for **FYOS — For Your Own Sake**: monthly plan, account balances and transactions from MoneyMoney.
The package contains no personal configuration and points at no machine that is not yours: the operator sets addresses
and credentials, and whatever is missing reports "not set up" (see Configuration; the plugin's own status texts are
German).

Tools: `finance.monat`, `finance.posten_bezahlt`, `finance.posten_aussetzen`, `finance.konten`, `finance.umsaetze`
(read-only except the two `posten_*`; the read views never pass on IBAN, reference or notes).

## Installing into the hub image

The package depends on `fyos-hub` and registers itself through the `mcp_hub.plugins` entry point (`finance`):

    pip install /path/to/fyos-plugin-finanzen        # or: pip install git+<repo-url>

In the Dockerfile of the hub image (after the hub install) add one line, `RUN pip install ./fyos-plugin-finanzen`. Without
an entry in the hub configuration the hub loads the plugin through its entry point. With an entry (configuration,
`enabled: false`):

    plugins:
      - name: finance
        config:
          finanzen_url: https://finanzen.example.org
          konten_url: https://konten.example.org

## Configuration

There is no default pointing at any service. Plugin `config:` takes precedence over the environment:

| config | environment | meaning |
|---|---|---|
| `finanzen_url` | `FYOS_FINANZEN_URL` | monthly-plan service (`monat`, `posten_bezahlt`, `posten_aussetzen`) |
| `konten_url` | `FYOS_FINANZEN_KONTEN_URL` | service holding the MoneyMoney data (`konten`, `umsaetze`) |
| `auth_header` | `FYOS_FINANZEN_AUTH_HEADER` | optional header line `Name: value` for calls to the services (no default) |

Anything not set (empty counts as not set) is **not set up**: read tools answer with `zustand: "nicht eingerichtet"` and the
way to set it up (`einrichten`), write tools with `ok: false` and the same hint, `health` reports
`status: "nicht eingerichtet"` (or "partly set up" if only one service is set). Invalid arguments are rejected regardless.
No request ever goes out into the void.

### Old names

The earlier names still work as aliases and are read when the new name is not set. If both are set, the new name wins.

| New name | Old name |
|---|---|
| `FYOS_FINANZEN_KONTEN_URL` | `FYOS_FINANZEN_LIFE_URL` |
| `konten_url` (config) | `life_url` |

The services are not part of this package; see `dienst/README.md` for the endpoints they have to offer.

## Mac side (`mac/`)

MoneyMoney runs on the Mac only. From there, transactions and balances go to the webhook twice a day.

| File | Purpose |
|---|---|
| `moneymoney_export.sh` | transactions (`export transactions`) → `POST <base>/api/life/webhook/moneymoney`, then auto-match and `moneymoney_konten.py` |
| `moneymoney_konten.py` | balances (`export accounts`) → `…/moneymoney-konten`; only the last four digits of the IBAN |
| `moneymoney_csv_watch.py` | CSV watcher → `FYOS_FINANZEN_URL` (or `MM_INGEST_URL`); optional `FYOS_FINANZEN_AUTH_HEADER`; without an address "not set up" |
| `moneymoney_import.py` | direct import into a Postgres database (`FYOS_FINANZEN_DB_URL`, old `FYOS_AUDIT_DB_URL`); `--reclassify` only with `FYOS_FINANZEN_RECLASSIFY_MODUL` |
| `moneymoney_export.lua`, `moneymoney_export.scpt` | older route (extension, AppleScript trigger) |
| `moneymoney-push.applescript` | slim push from inside MoneyMoney |
| `*.plist`, `install.sh` | launch agents; `install.sh` replaces `__MAC_DIR__` with the folder and puts them in `~/Library/LaunchAgents` |

Setup:

1. `mkdir -p ~/.config/fyos`; in it `finance_base_url` (one line, e.g. `https://fyos.example.org`) and `finance_token`
   (bearer token of the webhook; mode 600, never in the repo). Alternatively `FYOS_BASIS_URL`, `FYOS_FINANCE_TOKEN_FILE`.
2. Leave MoneyMoney unlocked (the scripts only read; no transfers).
3. `mac/install.sh` and `launchctl load -w ~/Library/LaunchAgents/de.fyos.moneymoney-sync.plist`.
4. Test: `bash mac/moneymoney_export.sh 7`.

## Tests

    uv pip install -e ".[test]"      # fyos-hub must be installed
    python3 -m pytest -q

`tests/test_plugin.py` covers the read and write paths (the network is a stub, addresses are documentation addresses on
`example.org`), the "not set up" state per tool and the environment names. `tests/test_paket.py` makes sure the package
contains no LAN address and no cluster service. `tests/test_mac_konten.py` covers the Mac side.

GitHub Actions (`.github/workflows/tests.yml`) runs the same tests against the hub from the source tree of
`fyos-os`, plus `tools/sauber.py` (no private addresses, accounts or secrets in the
tree or the history) and gitleaks.

## License

Apache License, Version 2.0 (SPDX identifier `Apache-2.0`). Copyright 2026 sirfyyn and FYOS CONTRIBUTORS <!-- sauber: ok -->

The full text is in [`LICENSE`](LICENSE), the copyright notice in [`NOTICE`](NOTICE).
