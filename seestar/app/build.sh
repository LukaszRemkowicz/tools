#!/bin/sh
# Builds ../seestar_download.app (universal). Env: SIGN_ID (default "Developer ID Application", "-" = ad-hoc), NOTARY_PROFILE.
set -e
cd "$(dirname "$0")"
APP="$(cd .. && pwd)/seestar_download.app"
VERSION="$(sed -n "s/^__version__ = '\(.*\)'.*/\1/p" ../seestar_download.py)"   # single source: the script
[ -n "$VERSION" ] || { echo "no __version__ in seestar_download.py" >&2; exit 1; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for arch in arm64 x86_64; do
    swiftc -O -target "$arch-apple-macos13.0" main.swift -o "$TMP/$arch"
done
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
lipo -create "$TMP/arm64" "$TMP/x86_64" -output "$APP/Contents/MacOS/seestar_download"
cp AppIcon.icns ../seestar_download.py "$APP/Contents/Resources/"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.seestar.download</string>
<key>CFBundleName</key><string>seestar_download</string>
<key>CFBundleDisplayName</key><string>seestar_download</string>
<key>CFBundleExecutable</key><string>seestar_download</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>$VERSION</string>
<key>CFBundleVersion</key><string>$VERSION</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
ID="${SIGN_ID:-Developer ID Application}"
if [ "$ID" != "-" ] && security find-identity -v -p codesigning | grep -q "$ID"; then
    codesign --force --options runtime --timestamp -s "$ID" "$APP"
else
    echo "warning: no signing identity '$ID', signing ad-hoc" >&2
    codesign --force -s - "$APP"
fi
if [ -n "$NOTARY_PROFILE" ]; then
    ditto -c -k --keepParent "$APP" "$TMP/app.zip"
    xcrun notarytool submit "$TMP/app.zip" --keychain-profile "$NOTARY_PROFILE" --wait | tee "$TMP/notary.txt"
    grep -q 'status: Accepted' "$TMP/notary.txt" || { echo "notarization not accepted; see xcrun notarytool log <id>" >&2; exit 1; }
    for i in 1 2 3 4 5; do   # the ticket can take a while to appear
        xcrun stapler staple "$APP" && break
        [ "$i" = 5 ] && exit 1
        sleep 30
    done
    xcrun stapler validate "$APP"
    spctl -a -vv -t exec "$APP"
else
    echo "not notarized: set NOTARY_PROFILE"
fi
"$APP/Contents/MacOS/seestar_download" --selftest
lipo -archs "$APP/Contents/MacOS/seestar_download"
echo "built $APP"
