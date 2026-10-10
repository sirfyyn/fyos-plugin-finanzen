(*
BL-MONEYMONEY-PIPELINE-001 — MoneyMoney Export-Trigger (AppleScript)

Wird von launchd (com.fyos.finance-sync.plist) 2x taeglich aufgerufen.

Flow:
  1. MoneyMoney.app starten (falls nicht laufend, oeffnet in background)
  2. "FYOS JSON" Exporter ausfuehren (via UI-Scripting — MoneyMoney bietet
     keinen direkten AppleScript-Export mit target-filter, daher
     verwenden wir die `export` CLI-Menu-Abkuerzung).
  3. JSON-File (/tmp/moneymoney-export-<ts>.json) lesen
  4. POST an <Basis-URL>/api/finance/import mit Bearer-Auth
     (Bearer kommt aus macOS-Keychain, key=fyos-finance-import-token)
  5. Bei HTTP-Fail: Kopie nach /Volumes/FYOS-Finance/inbox/ (SMB-Share)
  6. /tmp-File loeschen (Security: enthaelt IBANs + Betraege)
  7. Log nach /tmp/fyos-finance-sync.log

Vorbedingung:
  - MoneyMoney.app installiert + PSD2-Addon aktiv
  - FYOSExport.lua im MM-Extensions-Ordner
  - Keychain-Entry: `security add-generic-password -a fyos -s fyos-finance-import-token -w '<TOKEN>'`
  - Optional: SMB-Mount /Volumes/FYOS-Finance

Hinweise:
  - Skript darf nicht interaktiv sein (launchd) — bei 2FA-Prompt loggen wir Fehler und beenden.
  - "Accessibility"-Permission fuer osascript ggf. in macOS Settings > Privacy freigeben.
*)

on run
	set logFile to "/tmp/fyos-finance-sync.log"
	set tsSuffix to do shell script "date -u +%s"
	set exportPath to "/tmp/moneymoney-export-" & tsSuffix & ".json"
	-- Basis-URL der eigenen Installation: Datei ~/.config/fyos/finance_base_url (eine Zeile)
	set endpointURL to (do shell script "cat \"$HOME/.config/fyos/finance_base_url\"") & "/api/finance/import"
	set smbInbox to "/Volumes/FYOS-Finance/inbox"

	my appendLog(logFile, "=== FYOS finance-sync run " & tsSuffix & " ===")

	-- Step 1: MoneyMoney starten
	try
		tell application "MoneyMoney" to activate
		delay 3
	on error errMsg
		my appendLog(logFile, "ERROR: MoneyMoney activate failed: " & errMsg)
		return
	end try

	-- Step 2: Export triggern via UI-Scripting
	-- Siehe MoneyMoney-Docs: File > Export > "FYOS JSON" mit allen Konten.
	try
		tell application "System Events"
			tell process "MoneyMoney"
				-- Menu: Ablage (File) > Alle Konten exportieren... / Export All Accounts…
				-- Name varies by locale — wir versuchen beide.
				try
					click menu item "Alle Konten exportieren…" of menu "Ablage" of menu bar 1
				on error
					click menu item "Export All Accounts…" of menu "File" of menu bar 1
				end try
				delay 2
				-- Format-Popup auf "FYOS JSON", dann Speicherort auf exportPath
				-- (dialog-interaction waere hier lang — produktiver Weg:
				--  MoneyMoney ruft bei CLI-Export `mmoney-cli export` auf,
				--  das geht robuster ueber do shell script)
			end tell
		end tell

		-- Alternativ: MoneyMoney CLI (ab MM 2.4 verfuegbar)
		do shell script "/Applications/MoneyMoney.app/Contents/MacOS/MoneyMoney --export-all --format 'FYOS JSON' --output " & quoted form of exportPath & " 2>&1 || true"
		delay 2
	on error errMsg
		my appendLog(logFile, "ERROR: Export trigger failed: " & errMsg)
		return
	end try

	-- Step 3: Existenz + Groesse checken
	set fileExists to do shell script "test -s " & quoted form of exportPath & " && echo yes || echo no"
	if fileExists is not "yes" then
		my appendLog(logFile, "ERROR: Export file missing or empty: " & exportPath)
		return
	end if

	-- Step 4: Token aus Keychain holen
	set bearerToken to ""
	try
		set bearerToken to do shell script "security find-generic-password -a fyos -s fyos-finance-import-token -w 2>/dev/null"
	on error
		my appendLog(logFile, "ERROR: Keychain-Entry fyos-finance-import-token fehlt")
		return
	end try

	-- Step 5: POST an Endpoint
	set curlCmd to "curl -sS -o /tmp/fyos-finance-sync-response.json -w '%{http_code}' " & ¬
		"-X POST " & quoted form of endpointURL & ¬
		" -H 'Authorization: Bearer " & bearerToken & "'" & ¬
		" -H 'Content-Type: application/json'" & ¬
		" --data-binary @" & quoted form of exportPath & ¬
		" --max-time 60"
	set httpStatus to ""
	try
		set httpStatus to do shell script curlCmd
	on error errMsg
		my appendLog(logFile, "WARN: curl failed: " & errMsg)
	end try

	if httpStatus is "200" or httpStatus is "201" or httpStatus is "202" then
		my appendLog(logFile, "OK: Upload succeeded (HTTP " & httpStatus & ")")
	else
		my appendLog(logFile, "WARN: HTTP " & httpStatus & " — fallback to SMB")
		-- Step 5b: Fallback SMB
		try
			do shell script "mkdir -p " & quoted form of smbInbox & " && cp " & quoted form of exportPath & " " & quoted form of (smbInbox & "/")
			my appendLog(logFile, "OK: SMB-Fallback copy to " & smbInbox)
		on error errMsg
			my appendLog(logFile, "ERROR: SMB fallback failed: " & errMsg)
		end try
	end if

	-- Step 6: /tmp-File loeschen (PII)
	try
		do shell script "rm -f " & quoted form of exportPath
		my appendLog(logFile, "OK: Cleaned up " & exportPath)
	on error errMsg
		my appendLog(logFile, "WARN: Cleanup failed: " & errMsg)
	end try

	my appendLog(logFile, "=== finance-sync done ===")
end run

on appendLog(logFile, msg)
	set ts to do shell script "date -u +'%Y-%m-%dT%H:%M:%SZ'"
	do shell script "echo " & quoted form of (ts & " " & msg) & " >> " & quoted form of logFile
end appendLog
