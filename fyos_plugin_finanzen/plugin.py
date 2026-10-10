"""finance — Finanzen als Hub-Plugin.

    finance.monat             GET   <finanzen>/api/finance/month?month=JJJJ-MM          (lesend)
    finance.posten_bezahlt    PATCH <finanzen>/api/finance/month/{id}  {status: bezahlt}
    finance.posten_aussetzen  PATCH <finanzen>  (Monat ausgesetzt, Folgemonat Soll + Rate)
    finance.konten            GET   <konten>/api/life/webhook/finance-konten             (lesend, MoneyMoney)
    finance.umsaetze          GET   <konten>/api/life/webhook/finance-summary?days=N     (lesend, MoneyMoney)

Die Endpunkte nennt der Betreiber ausdrücklich, aus der Plugin-Konfiguration (`config:` im Hub) oder der Umgebung;
es gibt keine Vorgabe auf einen Dienst:

    finanzen_url / FYOS_FINANZEN_URL          Monatsplan-Dienst
    konten_url   / FYOS_FINANZEN_KONTEN_URL   Dienst mit den MoneyMoney-Daten (alt: life_url / FYOS_FINANZEN_LIFE_URL)
    auth_header  / FYOS_FINANZEN_AUTH_HEADER  Kopfzeile fuer die Aufrufe an die Dienste, "Name: Wert" (optional)

Ohne Adresse ist das Werkzeug „nicht eingerichtet“: lesende antworten mit diesem Zustand und dem Weg zur Einrichtung
(`einrichten`), schreibende mit `ok: false` und demselben Hinweis, `health` meldet „nicht eingerichtet“. Keine Anfrage
geht ins Leere hinaus. Ungültige Argumente werden vorher abgewiesen (ValueError).

IBAN, Referenz und Notizen der Posten gibt `finance.monat` nicht weiter.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from mcp_hub.plugin import HubPlugin, ToolDescriptor

from . import _umgebung

NICHT_EINGERICHTET = "nicht eingerichtet"
EINRICHTEN_FINANZEN = (
    "Den Monatsplan-Dienst angeben: FYOS_FINANZEN_URL in der Umgebung des Hubs oder finanzen_url in der Plugin-Konfiguration "
    "(zum Beispiel https://finanzen.example.org). Er muss /api/finance/month anbieten (siehe dienst/README.md)."
)
EINRICHTEN_KONTEN = (
    "Den Dienst mit den MoneyMoney-Daten angeben: FYOS_FINANZEN_KONTEN_URL in der Umgebung des Hubs oder konten_url in der "
    "Plugin-Konfiguration (alte Namen: FYOS_FINANZEN_LIFE_URL, life_url). Die Daten schickt der Export aus mac/ dorthin "
    "(siehe README)."
)

UMSATZ_TAGE_MAX = 90
UMSATZ_LIMIT_MAX = 200
POSTEN_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
MONAT = re.compile(r"^\d{4}-\d{2}$")
#: Lesesichten: so lange gilt eine Antwort als frisch (die Flaeche fragt jede Minute).
CACHE_S = 20.0

_AUTH: dict[str, str] = {}


def _kopfzeilen(auth_header: str) -> dict[str, str]:
    kopf = {"Accept": "application/json"}
    if ":" in auth_header:
        name, wert = auth_header.split(":", 1)
        if name.strip():
            kopf[name.strip()] = wert.strip()
    return kopf


def senden(basis: str, methode: str, pfad: str, koerper: Any, auth_header: str | None = None) -> Any:
    req = urllib.request.Request(
        f"{basis}{pfad}",
        data=json.dumps(koerper).encode(),
        method=methode,
        headers={**_kopfzeilen(auth_header if auth_header is not None else _AUTH.get("h", "")), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode() or "{}")


def holen(basis: str, pfad: str, timeout: float = 8.0, auth_header: str | None = None) -> Any:
    """GET an einen Dienst. Wirft bei Fehlern."""
    req = urllib.request.Request(
        f"{basis}{pfad}", headers=_kopfzeilen(auth_header if auth_header is not None else _AUTH.get("h", ""))
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def _zahl(x: Any) -> float | None:
    try:
        return None if x is None or isinstance(x, bool) else float(x)
    except (TypeError, ValueError):
        return None


def _kurz(text: Any, n: int) -> str | None:
    t = " ".join(str(text or "").split())
    if not t:
        return None
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def monatssicht(r: dict[str, Any]) -> dict[str, Any]:
    """Antwort von /api/finance/month → die Monatssicht (rein, ohne Netz).

    Posten: id, name, betrag, soll, hinweis, faellig_am, status (bezahlt | offen | anstehend | geplant | ruht |
    ausgesetzt), manuell, intervall, typ, restschuld, bezahlt_am. Einnahmen: id, name, betrag, status
    (erhalten | ausstehend | geplant | ruht), manuell, erhalten_am.
    """
    summ = r.get("summary") or {}
    posten = []
    for o in r.get("obligations") or []:
        treffer = o.get("match") or {}
        posten.append(
            {
                "id": o.get("id"),
                "name": o.get("posten") or "",
                "betrag": _zahl(o.get("betrag")),
                "soll": _zahl(o.get("soll")),
                "hinweis": _kurz(o.get("note"), 120),
                "faellig_am": o.get("faelligkeit") or None,
                "status": str(o.get("status") or ""),
                "manuell": bool(o.get("manuell")),
                "intervall": o.get("intervall") or None,
                "typ": o.get("typ") or None,
                "restschuld": _zahl(o.get("restschuld")),
                "bezahlt_am": treffer.get("datum") or None,
            }
        )
    einnahmen = []
    for e in r.get("income") or []:
        treffer = e.get("match") or {}
        einnahmen.append(
            {
                "id": e.get("id"),
                "name": e.get("quelle") or "",
                "betrag": _zahl(e.get("betrag")),
                "status": str(e.get("status") or ""),
                "manuell": bool(e.get("manuell")),
                "erhalten_am": treffer.get("datum") or None,
            }
        )
    return {
        "monat": r.get("month"),
        "aktuell": bool(r.get("is_current")),
        "posten": posten,
        "einnahmen": einnahmen,
        "summe": {
            k: _zahl(summ.get(k))
            for k in ("ausgaben_geplant", "ausgaben_bezahlt", "einnahmen_geplant", "einnahmen_erhalten", "saldo")
        }
        | {"restschuld": _zahl(summ.get("restschuld_total"))},
        "ueberfaellig": [str(x) for x in summ.get("ueberfaellig") or [] if x],
        "einnahmen_ausstehend": [str(x) for x in summ.get("einnahmen_ausstehend") or [] if x],
    }


def _alter_h(iso: Any, jetzt: dt.datetime) -> float | None:
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return round((jetzt - t).total_seconds() / 3600, 1)


def kontensicht(r: dict[str, Any], jetzt: dt.datetime | None = None) -> dict[str, Any]:
    """Antwort von /finance-konten → Konten mit Saldo und Alter des Stands (rein, ohne Netz)."""
    jetzt = jetzt or dt.datetime.now(dt.timezone.utc)
    konten = [
        {
            "name": k.get("name") or "",
            "typ": k.get("typ"),
            "saldo": _zahl(k.get("saldo")),
            "waehrung": k.get("waehrung") or "EUR",
            "iban_ende": k.get("iban_ende"),
            "bank_stand": k.get("aktualisiert"),
            "gruppe": bool(k.get("gruppe")),
        }
        for k in r.get("konten") or []
        if isinstance(k, dict)
    ]
    summe: dict[str, float] = {}
    for k in konten:
        if not k["gruppe"] and k["saldo"] is not None:
            summe[k["waehrung"]] = round(summe.get(k["waehrung"], 0.0) + k["saldo"], 2)
    alter = _alter_h(r.get("stand"), jetzt) if r.get("stand") else None
    if not konten:
        satz = "Noch keine Kontostände: der Mac-Export (MoneyMoney) hat noch keine geschickt."
    else:
        satz = f"{sum(1 for k in konten if not k['gruppe'])} Konten, zusammen " + ", ".join(
            f"{v:.2f} {w}" for w, v in summe.items()
        ) + (f"; Stand vor {alter:.0f} h" if alter is not None else "")
        if alter is not None and alter > 36:
            satz += " — veraltet: läuft MoneyMoney auf dem Mac (entsperrt)?"
    return {"konten": konten, "summe": summe, "stand": r.get("stand"), "alter_h": alter, "satz": satz}


def umsatzsicht(
    r: dict[str, Any],
    kontonamen: dict[str, str],
    *,
    suche: str = "",
    kategorie: str = "",
    konto: str = "",
    limit: int = 50,
    sortierung: str = "neu",
) -> dict[str, Any]:
    """Antwort von /finance-summary → gefilterte Umsätze mit Summen (rein, ohne Netz).

    sortierung "ausgaben": nur Ausgaben, größte zuerst.
    """
    s, kat, kto = suche.casefold(), kategorie.casefold(), konto.casefold()
    liste = []
    for t in r.get("transactions") or []:
        name = kontonamen.get(str(t.get("account") or ""), t.get("account") or None)
        if s and s not in f"{t.get('merchant')} {t.get('purpose')}".casefold():
            continue
        if kat and kat != str(t.get("category") or "").casefold():
            continue
        if kto and kto not in str(name or "").casefold():
            continue
        liste.append(
            {
                "datum": t.get("booking_date"),
                "betrag": _zahl(t.get("amount")),
                "waehrung": t.get("currency") or "EUR",
                "gegenueber": t.get("merchant"),
                "zweck": _kurz(t.get("purpose"), 160),
                "kategorie": t.get("category"),
                "konto": name,
            }
        )
    ein = round(sum(t["betrag"] for t in liste if (t["betrag"] or 0) > 0), 2)
    aus = round(-sum(t["betrag"] for t in liste if (t["betrag"] or 0) < 0), 2)
    nach_kategorie: dict[str, float] = {}
    for t in liste:
        k = t["kategorie"] or "Sonstiges"
        nach_kategorie[k] = round(nach_kategorie.get(k, 0.0) + (t["betrag"] or 0), 2)
    tage = (r.get("summary") or {}).get("days")
    gezeigt = liste
    if sortierung == "ausgaben":
        gezeigt = sorted((t for t in liste if (t["betrag"] or 0) < 0), key=lambda t: t["betrag"])
    return {
        "tage": tage,
        "anzahl": len(liste),
        "sortierung": sortierung,
        "gezeigt": min(len(gezeigt), limit),
        "umsaetze": gezeigt[:limit],
        "summe": {"ein": ein, "aus": aus, "saldo": round(ein - aus, 2)},
        "nach_kategorie": dict(sorted(nach_kategorie.items(), key=lambda kv: kv[1])),
        "satz": f"{len(liste)} Umsätze in {tage} Tagen: +{ein:.2f} / −{aus:.2f}",
    }


class _Frisch:
    """Kleiner TTL-Zwischenspeicher je Schluessel."""

    def __init__(self) -> None:
        self._d: dict[str, tuple[float, Any]] = {}

    def holen(self, key: str, laden: Callable[[], Any]) -> Any:
        e = self._d.get(key)
        if e and time.monotonic() - e[0] < CACHE_S:
            return e[1]
        wert = laden()
        self._d.pop(key, None)
        self._d[key] = (time.monotonic(), wert)
        if len(self._d) > 256:  # begrenzt: Abgelaufene raus, danach die Aeltesten
            jetzt = time.monotonic()
            for k in [k for k, (t, _w) in self._d.items() if jetzt - t >= CACHE_S]:
                del self._d[k]
            while len(self._d) > 256:
                del self._d[next(iter(self._d))]
        return wert

    def vergessen(self) -> None:
        self._d.clear()


class FinancePlugin(HubPlugin):
    name = "finance"
    description = (
        "Finanzen: Monatssicht mit allen Posten, Kontostände und Umsätze aus MoneyMoney (nur lesen); "
        "einen Posten als bezahlt markieren."
    )
    version = "0.5.0"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__()
        self.config = dict(config or {})
        self._senden = senden  # Tests setzen eine Attrappe
        self._holen = holen
        self._frisch = _Frisch()

    # -- Konfiguration (Plugin-config vor Umgebung; ohne beides: nicht eingerichtet) ----
    def _wert(self, schluessel: str, umgebung: str) -> str:
        """Adresse aus `config:` (neuer Schlüssel, sonst Alias), sonst aus der Umgebung (neuer Name, sonst Alias); leer = fehlt."""
        return (_umgebung.aus_config(self.config, schluessel) or _umgebung.lesen(umgebung)).rstrip("/")

    @property
    def finanzen_url(self) -> str:
        """Monatsplan-Dienst; leer, wenn nicht eingerichtet."""
        return self._wert("finanzen_url", "FYOS_FINANZEN_URL")

    @property
    def konten_url(self) -> str:
        """Dienst mit den MoneyMoney-Daten; leer, wenn nicht eingerichtet."""
        return self._wert("konten_url", "FYOS_FINANZEN_KONTEN_URL")

    @property
    def life_url(self) -> str:
        """Alter Name von `konten_url` (Alias)."""
        return self.konten_url

    def register(self) -> None:
        _AUTH["h"] = _umgebung.aus_config(self.config, "auth_header") or _umgebung.lesen("FYOS_FINANZEN_AUTH_HEADER")

    def health(self) -> dict[str, Any]:
        finanzen, konten = self.finanzen_url, self.konten_url
        out: dict[str, Any] = {"ok": True, "finanzen_url": finanzen, "konten_url": konten}
        if finanzen and konten:
            return {**out, "status": "ok"}
        if not finanzen and not konten:
            return {**out, "status": NICHT_EINGERICHTET, "einrichten": f"{EINRICHTEN_FINANZEN} {EINRICHTEN_KONTEN}"}
        # Nur ein Dienst eingerichtet: die Werkzeuge dieses Dienstes laufen, die des anderen sagen, was fehlt.
        return {**out, "status": "teilweise eingerichtet", "einrichten": EINRICHTEN_KONTEN if finanzen else EINRICHTEN_FINANZEN}

    @staticmethod
    def _nicht_eingerichtet(was: str, einrichten: str) -> dict[str, Any]:
        """Antwort lesender Werkzeuge ohne Adresse: der eigene Zustand und der Weg dahin, weder Fehler noch leere Liste."""
        return {"zustand": NICHT_EINGERICHTET, "eingerichtet": False,
                "satz": f"{was} ist nicht eingerichtet: keine Adresse des Dienstes. Weg: siehe `einrichten`.",
                "einrichten": einrichten}

    @staticmethod
    def _nicht_eingerichtet_ablehnen(was: str, einrichten: str) -> dict[str, Any]:
        """Antwort schreibender Werkzeuge ohne Adresse: nichts wird gesendet."""
        return {"ok": False, "zustand": NICHT_EINGERICHTET, "eingerichtet": False,
                "error": f"{was} ist nicht eingerichtet, es wurde nichts geändert. {einrichten}", "einrichten": einrichten}

    def describe_tools(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name="monat",
                title="Monat (Finanzen)",
                description=(
                    "Alle Posten des Monats mit Status (bezahlt, offen = faellig und nicht bezahlt, "
                    "anstehend, geplant, ruht), Betrag, Faelligkeit, Restschuld und wann bezahlt; dazu "
                    "die Einnahmen mit Status und die Summen. `monat` JJJJ-MM (leer = jetzt). Ohne IBAN "
                    "und Referenz. Nur lesend, 20 s zwischengespeichert."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"monat": {"type": "string", "description": "JJJJ-MM, leer = aktueller Monat"}},
                    "additionalProperties": False,
                },
                tags=["finance", "read", "flaeche"],
            ),
            ToolDescriptor(
                name="posten_bezahlt",
                title="Posten bezahlt",
                description=(
                    "Markiert einen Posten des Monatsplans als bezahlt (manuelle Uebersteuerung im "
                    "Finanzdienst, umkehrbar). `id` aus finance.monat, "
                    "`betrag` = tatsaechlich gezahlt, falls abweichend; `monat` JJJJ-MM (leer = jetzt)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "betrag": {"type": "number", "minimum": 0},
                        "monat": {"type": "string"},
                    },
                    "required": ["id"],
                    "additionalProperties": False,
                },
                tags=["finance", "write", "flaeche"],
            ),
            ToolDescriptor(
                name="posten_aussetzen",
                title="Posten aussetzen",
                description=(
                    "Setzt einen Posten im Monatsplan aus (Status ausgesetzt: nicht geplant, nicht "
                    "ueberfaellig, Restschuld unveraendert) und holt ihn auf Wunsch spaeter nach: im Monat "
                    "`nachholen_in` steigt das Soll um eine Rate (2 x Betrag). Bewegt KEIN Geld, nur der "
                    "Plan, umkehrbar. `id` aus finance.monat, `monat` und `nachholen_in` JJJJ-MM."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "monat": {"type": "string", "description": "JJJJ-MM des ausgesetzten Monats"},
                        "nachholen_in": {"type": "string", "description": "JJJJ-MM des Nachhol-Monats (spaeter)"},
                    },
                    "required": ["id", "monat"],
                    "additionalProperties": False,
                },
                tags=["finance", "write", "flaeche"],
            ),
            ToolDescriptor(
                name="konten",
                title="Kontostände",
                description=(
                    "Kontostände aus MoneyMoney (Mac-Export, zweimal täglich): je Konto Name, Art, Saldo, Währung, "
                    "letzte vier IBAN-Stellen, Stand der Bank; Summe je Währung und wie alt der Stand ist. "
                    "Nur lesend, keine Überweisungen."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                tags=["finance", "read", "flaeche"],
            ),
            ToolDescriptor(
                name="umsaetze",
                title="Umsätze",
                description=(
                    "Umsätze aus MoneyMoney, neueste zuerst: Datum, Betrag (Ausgaben negativ), Gegenüber, Zweck, "
                    "Kategorie, Konto; dazu Summen ein/aus und je Kategorie. Filter: `tage` (1–90, Vorgabe 30), "
                    "`suche` (Gegenüber/Zweck), `kategorie`, `konto` (Teil des Namens), `limit` (Vorgabe 50). "
                    "`sortierung: \"ausgaben\"` zeigt nur Ausgaben, größte zuerst. Nur lesend."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "tage": {"type": "integer", "minimum": 1, "maximum": UMSATZ_TAGE_MAX, "default": 30},
                        "suche": {"type": "string"},
                        "kategorie": {"type": "string"},
                        "konto": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": UMSATZ_LIMIT_MAX, "default": 50},
                        "sortierung": {"type": "string", "enum": ["neu", "ausgaben"], "default": "neu"},
                    },
                    "additionalProperties": False,
                },
                tags=["finance", "read", "flaeche"],
            ),
        ]

    def call(self, method: str, args: dict[str, Any]) -> Any:
        args = args or {}
        if method == "monat":
            monat = args.get("monat") or dt.date.today().strftime("%Y-%m")
            if not isinstance(monat, str) or not MONAT.match(monat):
                raise ValueError("monat: Form JJJJ-MM")
            if not self.finanzen_url:
                return self._nicht_eingerichtet("Der Monatsplan", EINRICHTEN_FINANZEN)
            return self._frisch.holen(monat, lambda: self._monat(monat))
        if method == "konten":
            konten = self.konten_url
            if not konten:
                return self._nicht_eingerichtet("Die Kontenübersicht", EINRICHTEN_KONTEN)
            return kontensicht(
                self._frisch.holen("konten", lambda: self._holen(konten, "/api/life/webhook/finance-konten"))
            )
        if method == "umsaetze":
            return self._umsaetze(args)
        if method == "posten_aussetzen":
            return self._aussetzen(args)
        if method != "posten_bezahlt":
            raise ValueError(f"finance hat kein Werkzeug {method!r}")
        id_ = args.get("id")
        if not isinstance(id_, str) or not POSTEN_ID.match(id_):
            raise ValueError("id ungueltig")
        monat = args.get("monat") or dt.date.today().strftime("%Y-%m")
        if not isinstance(monat, str) or not MONAT.match(monat):
            raise ValueError("monat: Form JJJJ-MM")
        koerper: dict[str, Any] = {"status": "bezahlt", "note": "Zentrale"}
        betrag = args.get("betrag")
        if betrag is not None:
            if isinstance(betrag, bool) or not isinstance(betrag, (int, float)) or betrag < 0:
                raise ValueError("betrag muss eine Zahl >= 0 sein")
            koerper["betrag_actual"] = betrag
        basis = self.finanzen_url
        if not basis:
            return self._nicht_eingerichtet_ablehnen("Der Monatsplan", EINRICHTEN_FINANZEN)
        pfad = f"/api/finance/month/{urllib.parse.quote(id_)}?month={monat}"
        antwort = self._senden(basis, "PATCH", pfad, koerper)
        self._frisch.vergessen()  # die naechste Monatssicht zeigt den Haken sofort
        return antwort

    def _posten_im_monat(self, monat: str, id_: str) -> dict[str, Any]:
        """Frisch vom Dienst (nicht aus dem 20-s-Speicher): der Posten in der Monatssicht."""
        sicht = monatssicht(self._holen(self.finanzen_url, f"/api/finance/month?month={monat}"))
        for p in sicht["posten"]:
            if p["id"] == id_:
                return p
        raise ValueError(f"Posten {id_} steht im Monat {monat} nicht im Plan")

    def _aussetzen(self, args: dict[str, Any]) -> dict[str, Any]:
        """Posten im Monat aussetzen, optional im Folgemonat nachholen (nur Monatsplan, kein Geld)."""
        id_ = args.get("id")
        if not isinstance(id_, str) or not POSTEN_ID.match(id_):
            raise ValueError("id ungueltig")
        monat, nach = args.get("monat"), args.get("nachholen_in")
        if not isinstance(monat, str) or not MONAT.match(monat):
            raise ValueError("monat: Form JJJJ-MM")
        if nach is not None and (not isinstance(nach, str) or not MONAT.match(nach) or nach <= monat):
            raise ValueError("nachholen_in: Form JJJJ-MM, nach dem ausgesetzten Monat")
        if not self.finanzen_url:
            return self._nicht_eingerichtet_ablehnen("Der Monatsplan", EINRICHTEN_FINANZEN)
        pfad = f"/api/finance/month/{urllib.parse.quote(id_)}?month="
        notiz = f"ausgesetzt {monat}, nachgeholt"
        nachhol_koerper = None
        if nach:  # erst alles lesen und pruefen, dann schreiben
            aus = self._posten_im_monat(monat, id_)
            ziel = self._posten_im_monat(nach, id_)
            if ziel["status"] in ("bezahlt", "ruht"):
                raise ValueError(f"Im Monat {nach} ist der Posten {ziel['status']}: nichts nachzuholen")
            if (ziel.get("hinweis") or "").startswith(notiz):
                nachhol_koerper = {}  # schon nachgeholt: nicht ein zweites Mal aufschlagen
            else:
                rate = aus["betrag"] or 0
                soll = ziel["soll"] if ziel.get("soll") is not None else (ziel["betrag"] or 0)
                nachhol_koerper = {
                    "status": "offen" if ziel["manuell"] and ziel["status"] == "offen" else "auto",
                    "betrag_actual": round(soll + rate, 2),
                    "note": notiz,
                }
        basis = self.finanzen_url
        antwort: dict[str, Any] = {
            "ausgesetzt": self._senden(basis, "PATCH", pfad + monat, {"status": "ausgesetzt", "note": notiz})
        }
        if nachhol_koerper:
            antwort["nachgeholt"] = self._senden(basis, "PATCH", pfad + nach, nachhol_koerper)
        elif nach:
            antwort["nachgeholt"] = {"ok": True, "schon_erledigt": True}
        self._frisch.vergessen()
        return antwort

    def _umsaetze(self, args: dict[str, Any]) -> dict[str, Any]:
        tage, limit = args.get("tage", 30), args.get("limit", 50)
        for name, wert, hoechst in (("tage", tage, UMSATZ_TAGE_MAX), ("limit", limit, UMSATZ_LIMIT_MAX)):
            if isinstance(wert, bool) or not isinstance(wert, int) or not 1 <= wert <= hoechst:
                raise ValueError(f"{name}: ganze Zahl 1–{hoechst}")
        sortierung = args.get("sortierung") or "neu"
        if sortierung not in ("neu", "ausgaben"):
            raise ValueError("sortierung: neu oder ausgaben")
        life = self.konten_url
        if not life:
            return self._nicht_eingerichtet("Die Umsätze", EINRICHTEN_KONTEN)
        r = self._frisch.holen(
            f"umsaetze:{tage}", lambda: self._holen(life, f"/api/life/webhook/finance-summary?days={tage}")
        )
        try:
            k = self._frisch.holen("konten", lambda: self._holen(life, "/api/life/webhook/finance-konten"))
            namen = {str(x.get("uuid")): x.get("name") for x in k.get("konten") or [] if x.get("uuid")}
        except Exception:  # noqa: BLE001 — ohne Kontonamen bleibt die UUID stehen
            namen = {}
        return umsatzsicht(
            r, namen, suche=str(args.get("suche") or ""), kategorie=str(args.get("kategorie") or ""),
            konto=str(args.get("konto") or ""), limit=limit, sortierung=sortierung,
        )

    def _monat(self, monat: str) -> dict[str, Any]:
        r = self._holen(self.finanzen_url, f"/api/finance/month?month={monat}")
        if not isinstance(r, dict) or not isinstance(r.get("obligations"), list):
            raise ValueError("Antwort ohne obligations")
        return monatssicht(r)
