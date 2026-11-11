# PathBar

Always-visible path bar for macOS Open/Save dialogs. macOS shows the path there only while you hold ⌥; PathBar keeps it on screen, glued under the dialog. Click the bar to copy the path.

It reads the dialog through the Accessibility API. No code injection, SIP stays on.

## How to

### 1. Create a signing certificate (once)

Without a stable signature macOS forgets the Accessibility permission after every rebuild. A self-signed certificate in your login keychain fixes that. It is used only by `codesign`, is not trusted for anything else, and works only on this Mac.

```sh
T=$(mktemp -d) && cd "$T"
cat > cert.cnf <<'EOF'
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = PathBar Local Signing
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
EOF
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 3650 -config cert.cnf
P=$(openssl rand -hex 12)
openssl pkcs12 -export -inkey key.pem -in cert.pem -out id.p12 -passout pass:$P \
  -certpbe PBE-SHA1-3DES -keypbe PBE-SHA1-3DES -macalg sha1
security import id.p12 -k ~/Library/Keychains/login.keychain-db -P $P -T /usr/bin/codesign
cd / && rm -rf "$T"
```

`security find-identity -p codesigning` will list it as `CSSMERR_TP_NOT_TRUSTED`. That is expected and signing still works.

Skip this step and the app is ad-hoc signed: it works, but you must remove and re-add it in Accessibility after every rebuild.

### 2. Build and run

```sh
./build.sh                       # builds ~/Applications/PathBar.app, signs it, runs a self-test
open ~/Applications/PathBar.app  # menu bar icon "⌥/"
```

On the first build macOS asks: **codesign wants to access key "id" in your keychain**. "id" is the certificate's private key from step 1. Enter your Mac login password and click **Always Allow**, otherwise the build waits on this prompt every time.

To sign with a different certificate: `SIGN_ID="Apple Development: …" ./build.sh`.

### 3. Grant Accessibility (once)

System Settings → Privacy & Security → Accessibility (on newer macOS: **Device Control and Data Access**) → **+** → press ⇧⌘G, paste `~/Applications/PathBar.app` → Open. Make sure the switch is on.

A ⚠︎ next to the menu bar icon (`⌥/ ⚠︎`) means the permission is missing.

### 4. Use it

- Open any Open/Save dialog: the path appears under it (or above, if there is no room below).
- Click the bar to copy the path.
- Screenshots: ⇧⌘4 and drag over the dialog and the bar. (⇧⌘4 + Space captures a single window, so it leaves the bar out.)
- Quit: menu bar icon → Quit PathBar.

PathBar adds itself to Login Items on first launch. Turn it off in System Settings → General → Login Items; it will not re-add itself.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `⌥/ ⚠︎` in the menu bar | Permission missing: select PathBar in the Accessibility list, click **−**, then add it again with **+**. |
| Build hangs | The keychain prompt from step 2 is waiting behind other windows. |
| No bar under a dialog | Is PathBar running (menu bar icon)? Is the folder empty? See known limits. |

## Uninstall

```sh
pkill -x PathBar
rm -rf ~/Applications/PathBar.app
security delete-identity -c "PathBar Local Signing"   # removes the certificate and its key
```

Then remove PathBar from Login Items and from the Accessibility list in System Settings.

## Known limits

- Empty folder: nothing to read the path from, so no bar.
- Column view with nothing selected: may show the first column's folder.
- A folder whose only item is an expanded subfolder (list view): may show the subfolder.
- Sandboxed apps (TextEdit, App Store apps): not tested yet.
