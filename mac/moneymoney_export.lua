-- BL-MONEYMONEY-PIPELINE-001 — MoneyMoney Export-Extension
--
-- Exportiert alle aktiven Konten (Giro + Kreditkarten + Schulden/Kredite) als
-- JSON, kompatibel zu moneymoney_import.py in diesem Ordner.
--
-- Installation:
--   ~/Library/Containers/com.moneymoney-app.retail/Data/Library/
--     Application Support/MoneyMoney/Extensions/FYOSExport.lua
--   (bzw. ~/Library/Application Support/MoneyMoney/Extensions/FYOSExport.lua
--    fuer non-App-Store Builds)
--
-- Nach dem Kopieren: MoneyMoney > Konto > Exportieren > "FYOS JSON"
-- oder via AppleScript (moneymoney_export.scpt).
--
-- Output:
--   /tmp/moneymoney-export-<unixts>.json  (chmod 600, via AppleScript nach Upload geloescht)
--
-- Format:
--   {
--     "schema_version": "1.0",
--     "exported_at": "<ISO8601 UTC>",
--     "mm_version": "<MM Version>",
--     "accounts": [
--       {
--         "account_id":   "<MM stable ID>",
--         "name":         "N26 Girokonto",
--         "owner":        "Vorname Nachname",
--         "account_number":"DE89********1234",   -- IBAN maskiert
--         "bank_code":    "",
--         "currency":     "EUR",
--         "balance":      1234.56,
--         "balance_date": "<ISO8601>",
--         "account_type": "giro|creditcard|loan|savings|portfolio",
--         "is_loan":      false,
--         "transactions": [
--           {
--             "id":            "<MM stable tx ID>",
--             "booking_date":  "YYYY-MM-DD",
--             "value_date":    "YYYY-MM-DD",
--             "amount":        -12.34,
--             "currency":      "EUR",
--             "purpose":       "REWE MARKT GMBH",
--             "name":          "REWE Group",
--             "category":      "Lebensmittel",      -- MM-Kategorie, der Dienst kategorisiert neu
--             "booking_text":  "LASTSCHRIFT",
--             "booked":        true,
--             "checkmark":     false
--           }
--         ]
--       }
--     ]
--   }

WebBanking {
  version     = 1.0,
  country     = "de",
  url         = "",
  services    = {},
  description = "FYOS JSON Export (nicht fuer Banking-Zugriff — dummy)"
}

Exporter {
  version     = 1.0,
  format      = "FYOS JSON",
  fileExtension = "json",
  description = "Exportiert Konten + Transactions als FYOS-kompatibles JSON"
}

local function iso8601(ts)
  -- MoneyMoney-Timestamps sind Unix-Epoch (seconds).
  if ts == nil or ts == 0 then return nil end
  return os.date("!%Y-%m-%dT%H:%M:%SZ", ts)
end

local function iso_date(ts)
  if ts == nil or ts == 0 then return nil end
  return os.date("!%Y-%m-%d", ts)
end

local function mask_iban(iban)
  if iban == nil or iban == "" then return "" end
  if #iban < 8 then return iban end
  return iban:sub(1, 4) .. string.rep("*", #iban - 8) .. iban:sub(-4)
end

local function escape_json(s)
  if s == nil then return "null" end
  s = tostring(s)
  s = s:gsub("\\", "\\\\")
  s = s:gsub('"', '\\"')
  s = s:gsub("\n", "\\n")
  s = s:gsub("\r", "\\r")
  s = s:gsub("\t", "\\t")
  return '"' .. s .. '"'
end

local function num_or_null(n)
  if n == nil then return "null" end
  return string.format("%.2f", n)
end

local function bool_json(b)
  if b == true then return "true" else return "false" end
end

local function account_type_from_mm(acc)
  -- MoneyMoney account.type ist ein localized string. Wir mappen robust auf
  -- FYOS-Types (giro/creditcard/loan/savings/portfolio/other).
  local t = (acc.type or ""):lower()
  if t:find("kredit") and t:find("karte") then return "creditcard" end
  if t:find("credit") and t:find("card") then return "creditcard" end
  if t:find("giro") or t:find("checking") then return "giro" end
  if t:find("spar") or t:find("saving") then return "savings" end
  if t:find("darleh") or t:find("loan") or t:find("kredit") then return "loan" end
  if t:find("depot") or t:find("portfolio") then return "portfolio" end
  return "other"
end

local function serialize_tx(tx)
  local parts = {}
  table.insert(parts, '"id":' .. escape_json(tx.id or tx.transactionCode or ""))
  table.insert(parts, '"booking_date":' .. escape_json(iso_date(tx.bookingDate)))
  table.insert(parts, '"value_date":' .. escape_json(iso_date(tx.valueDate)))
  table.insert(parts, '"amount":' .. num_or_null(tx.amount))
  table.insert(parts, '"currency":' .. escape_json(tx.currency or "EUR"))
  table.insert(parts, '"purpose":' .. escape_json(tx.purpose or ""))
  table.insert(parts, '"name":' .. escape_json(tx.name or ""))
  table.insert(parts, '"category":' .. escape_json(tx.category or ""))
  table.insert(parts, '"booking_text":' .. escape_json(tx.bookingText or ""))
  table.insert(parts, '"booked":' .. bool_json(tx.booked))
  table.insert(parts, '"checkmark":' .. bool_json(tx.checkmark))
  return "{" .. table.concat(parts, ",") .. "}"
end

local function serialize_account(acc, transactions)
  local parts = {}
  table.insert(parts, '"account_id":' .. escape_json(acc.uuid or acc.accountNumber))
  table.insert(parts, '"name":' .. escape_json(acc.name or ""))
  table.insert(parts, '"owner":' .. escape_json(acc.owner or ""))
  table.insert(parts, '"account_number":' .. escape_json(mask_iban(acc.accountNumber or "")))
  table.insert(parts, '"bank_code":' .. escape_json(acc.bankCode or ""))
  table.insert(parts, '"currency":' .. escape_json(acc.currency or "EUR"))
  table.insert(parts, '"balance":' .. num_or_null(acc.balance))
  table.insert(parts, '"balance_date":' .. escape_json(iso8601(acc.balanceDate)))
  table.insert(parts, '"account_type":' .. escape_json(account_type_from_mm(acc)))
  -- Schulden/Kredite: negativer Saldo ODER account_type=loan.
  local is_loan = (account_type_from_mm(acc) == "loan") or
                  (acc.balance ~= nil and acc.balance < 0 and acc.type and
                   (acc.type:lower():find("darlehen") or acc.type:lower():find("kredit")))
  table.insert(parts, '"is_loan":' .. bool_json(is_loan))
  local tx_strings = {}
  for _, tx in ipairs(transactions or {}) do
    table.insert(tx_strings, serialize_tx(tx))
  end
  table.insert(parts, '"transactions":[' .. table.concat(tx_strings, ",") .. "]")
  return "{" .. table.concat(parts, ",") .. "}"
end

-- MoneyMoney ruft WriteHeader/WriteAccounts/WriteTail in Reihenfolge auf.
-- Wir buffern alles und schreiben im Tail einen einzigen JSON-Blob.

local buffer_accounts = {}

function WriteHeader(account, startDate, endDate, transactionCount)
  -- Wird pro Account aufgerufen, zusammen mit WriteTransactions.
end

function WriteTransactions(account, transactions)
  table.insert(buffer_accounts, serialize_account(account, transactions))
end

function WriteTail(account)
  -- Kein per-account-Tail noetig.
end

-- Wird am Ende genau einmal aufgerufen (auch wenn MoneyMoney pro Konto loopt
-- sammeln wir im buffer und schreiben am Schluss).
-- Note: MoneyMoney ruft WriteTail pro Konto auf, das finale flush passiert
-- durch den Aufrufer. Wir emittieren daher beim letzten WriteTail den vollen
-- JSON-Blob. Um das zuverlaessig zu tun, flushen wir idempotent auch bei
-- jedem WriteTail — der Consumer (AppleScript) liest die datei erst nach
-- MoneyMoney-Exit.

-- MoneyMoney-Exporter schreibt via assert(io.write(...)). Wir nutzen einen
-- internen State um nur beim letzten Account zu schreiben.

local did_flush = false

local function flush_if_needed()
  if did_flush then return end
  did_flush = true
  local blob = '{"schema_version":"1.0",'
  blob = blob .. '"exported_at":"' .. os.date("!%Y-%m-%dT%H:%M:%SZ") .. '",'
  blob = blob .. '"mm_version":"' .. (MM and MM.productVersion or "unknown") .. '",'
  blob = blob .. '"accounts":[' .. table.concat(buffer_accounts, ",") .. "]"
  blob = blob .. "}"
  assert(io.write(blob))
end

-- Der MoneyMoney-Exporter-Contract: assert(io.write()) wird im WriteTail
-- erwartet. Damit der letzte WriteTail-Call flusht, ueberschreiben wir
-- die urspruengliche WriteTail.
function WriteTail(account)
  flush_if_needed()
end
