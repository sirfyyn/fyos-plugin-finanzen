---
name: finanzen
description: Laden bei Fragen zu Geld, Konten, Umsätzen oder dem Monatsplan (was ist offen, was ist bezahlt, wofür ging Geld weg). Welche finance-Werkzeuge in welcher Reihenfolge, was nie getan wird.
ausloeser: Kontostand, Umsätze, Monatsplan, offen, bezahlt, aussetzen, Ausgaben, MoneyMoney
fuer: [agent]
---

# Finanzen: Monatsplan, Konten, Umsätze

Dieser Skill gehört zum Plugin `fyos-plugin-finanzen`. Die Zahlen kommen aus MoneyMoney auf dem Mac des
Besitzers, der sie ein- bis zweimal täglich schickt. Nichts hier bewegt Geld.

## Wann welches Werkzeug

| Frage | Werkzeug |
|---|---|
| Was ist diesen Monat offen, was ist bezahlt, was steht an? | `finance.monat` |
| Wie stehen die Konten? | `finance.konten` |
| Wofür ging Geld weg, was kam rein, wer hat abgebucht? | `finance.umsaetze` (`sortierung: "ausgaben"` für die größten Posten) |
| Ein Posten ist bezahlt, steht aber noch offen | `finance.posten_bezahlt` |
| Ein Posten soll diesen Monat ausfallen und später nachgeholt werden | `finance.posten_aussetzen` |

## Ablauf

1. Für alles im Monatsplan zuerst `finance.monat` lesen: Die `id` der Posten kommt nur von dort.
2. Vor jedem schreibenden Aufruf (`posten_bezahlt`, `posten_aussetzen`) sagen, welcher Posten in welchem Monat
   geändert wird, und auf ein Ja warten. Beide sind umkehrbar, ändern aber den Plan des Besitzers.
3. Beim Aussetzen den Nachhol-Monat nennen: Dort steigt das Soll um eine Rate.
4. Bei `finance.konten` das Alter des Stands mitsagen, wenn er älter als einen Tag ist: Dann hat der Mac
   länger nichts geschickt.

## Regeln

- Keine Überweisungen, keine Bankaufträge. Dafür gibt es kein Werkzeug, und es wird auch nicht nachgebaut.
- IBAN, Referenzen und Notizen nicht weitergeben; die Werkzeuge liefern höchstens die letzten vier IBAN-Stellen.
- Beträge so nennen, wie sie kommen (Ausgaben negativ); nicht runden, wenn jemand nach einem genauen Betrag fragt.
- Ist der Datendienst nicht erreichbar, das sagen und nicht aus alten Antworten raten.
