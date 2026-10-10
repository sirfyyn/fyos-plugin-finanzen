(*
  MoneyMoney → FYOS-Webhook (AppleScript).

  Setup (am Mac):
    1. Datei in ~/Library/Application Scripts/com.moneymoney-app.retail/
    2. MoneyMoney → Hilfsmittel → AppleScript-Editor → diesen Script
    3. Optional: cron schedule via launchd plist

  Sources: MoneyMoney "Letzte 24h" via Bordmittel-Export.
*)

-- Basis-URL der eigenen Installation: Datei ~/.config/fyos/finance_base_url (eine Zeile)
set webhookURL to (do shell script "cat \"$HOME/.config/fyos/finance_base_url\"") & "/api/life/webhook/moneymoney"
set tokenFile to (POSIX path of (path to home folder)) & ".fyos-webhook-token"
set webhookToken to do shell script "cat " & quoted form of tokenFile

tell application "MoneyMoney"
  set transactions to export transactions from category "Alle" from date (current date - 1 * days) to date (current date) as "plist"
end tell

-- Convert plist → JSON via Mac-bordmittel (plutil)
set tmpPlist to (POSIX path of (path to temporary items)) & "mm-tx.plist"
set tmpJson to (POSIX path of (path to temporary items)) & "mm-tx.json"

do shell script "echo " & quoted form of transactions & " > " & quoted form of tmpPlist
do shell script "plutil -convert json -o " & quoted form of tmpJson & " " & quoted form of tmpPlist

-- POST with token
set curlCmd to "curl -fsS -X POST " & quoted form of webhookURL & ¬
  " -H 'Authorization: Bearer " & webhookToken & "'" & ¬
  " -H 'Content-Type: application/json'" & ¬
  " --data-binary @" & quoted form of tmpJson
do shell script curlCmd

return "MoneyMoney push completed " & (current date)
