#!/bin/sh
# Builds PathBar.app into ~/Applications, signed with $SIGN_ID (default "PathBar Local Signing") or ad-hoc if missing.
set -e
cd "$(dirname "$0")"
APP="$HOME/Applications/PathBar.app"
mkdir -p "$APP/Contents/MacOS"
swiftc -O main.swift -o "$APP/Contents/MacOS/PathBar"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.pathbar</string>
<key>CFBundleName</key><string>PathBar</string>
<key>CFBundleExecutable</key><string>PathBar</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.2</string>
<key>LSUIElement</key><true/>
</dict></plist>
PLIST
# A stable certificate keeps the Accessibility grant across rebuilds; ad-hoc ("-") loses it every time.
ID="${SIGN_ID:-PathBar Local Signing}"
security find-certificate -c "$ID" >/dev/null 2>&1 || ID=-
codesign -s "$ID" -f "$APP"
"$APP/Contents/MacOS/PathBar" --selftest
echo "built $APP"
