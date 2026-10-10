#!/bin/bash
# Legt die LaunchAgents mit dem richtigen Pfad ab: __MAC_DIR__ -> Ordner dieser Datei.
# Aufruf: mac/install.sh [plist-name ...]   (ohne Angabe: alle drei)
set -eu
MAC_DIR="$(cd "$(dirname "$0")" && pwd)"
ZIEL="$HOME/Library/LaunchAgents"
mkdir -p "$ZIEL"
PLISTS=("$@")
if [ ${#PLISTS[@]} -eq 0 ]; then
    PLISTS=(com.fyos.finance-sync.plist com.fyos.moneymoney-bridge.plist de.fyos.moneymoney-sync.plist)
fi
for p in "${PLISTS[@]}"; do
    sed "s#__MAC_DIR__#$MAC_DIR#g" "$MAC_DIR/$p" > "$ZIEL/$p"
    echo "geschrieben: $ZIEL/$p  (laden: launchctl load -w \"$ZIEL/$p\")"
done
