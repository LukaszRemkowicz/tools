# seestar_download

Download photos from a ZWO Seestar through the connection the official Seestar app already has open, for example to
a scope in a remote observatory reached over Telescope Network.

## Why

Over a remote connection the app downloads slowly and starts a file over whenever the link drops, which happens often.
This script resumes every file from the last byte it received and waits for you to reconnect the app.

## Usage

1. Open the Seestar app **on this Mac** and connect to the scope as usual.
2. Open Terminal: press `⌘ Space`, type **Terminal** and press Return. Go to this `tools` folder: type `cd `, drag
   the folder onto the Terminal window and press Return.
3. Download only, the photos stay on the scope:

       python3 seestar/seestar_download.py

   Or download and delete each photo from the scope once its copy here is complete:

       python3 seestar/seestar_download.py --delete

   Files go to `~/Pictures/Seestar/<album folder>/`. Options: `-o DIR` / `--output DIR` (where the photos go),
   `--folder NAME` (only that album folder, repeatable, e.g. `--folder "NGC 292"`), `--keep-one-sub` (with
   `--delete`, see below).
4. Stop with Ctrl-C at any time. Run the same command again to continue. Finished files are skipped and a half-done
   file continues from its `.part`.

The first lines show the target folder and the mode: `download_only` (default) or `download_and_delete`.

`--delete` (mode `download_and_delete`) frees the scope's storage as you go. Each file is deleted from the scope right
after its copy here is complete. Files downloaded in an earlier run are deleted too, but only after a `HEAD` request
confirms the copy here has the scope's exact size. JPG previews of stacks are never deleted by name; the scope may
remove them together with their FIT. Deleting cannot be undone.

`--keep-one-sub` with `--delete` leaves the last frame of every `*_sub` folder on the scope, which it needs for GoTo.
That frame is still downloaded.

## Mac app

`seestar/seestar_download.app` does the same without Terminal: open it, tick **Delete from the scope after
download** and, if wanted, **Keep the last sub frame on the scope (for GoTo)**, then press Start. It runs the copy of
`seestar_download.py` inside the app. It needs `python3` 3.9 or newer; if macOS offers to install the Command Line
Developer Tools on first start, accept. After changing `seestar_download.py`, rebuild the app with
`seestar/app/build.sh`.

If the app disconnects, the script waits and checks every 30 s. Reconnect the app and it carries on by itself. The
script cannot reconnect the app itself, so it shows a macOS notification when the app loses the scope and another when
it is back.

## How it works

While the app is connected it serves the scope's ports on this computer:

- `127.0.0.1:4700` is the scope's JSON-RPC control port. `get_albums` lists the album folders under `MyWorks`, and
  `get_albums "MyWorks/<folder>"` lists one folder. `delete_image {"name": ["MyWorks/<folder>/<file>"]}` deletes a
  file; it is the call the app's album makes (found in the app, tested on one file).
- `127.0.0.1:40080` is the scope's HTTP file server. Files are at `/MyWorks/<folder>/<file>`, and it supports `Range`.

Each file is written to `NAME.part`. After a drop it is re-requested with `Range: bytes=<size>-` and renamed once it
has the full `Content-Length`. Result folders are downloaded first, then the `*_sub` folders with single frames, which
are large.

## Notes

- **Speed.** Measured 50–650 KB/s through Telescope Network, so a 50 MB stacked FIT takes about 2–15 minutes.
- **JPG previews.** The JPG next to a stacked FIT is not in the folder listing, so its name is derived from the FIT's.
  The scope does not answer requests for missing files, so a missing JPG costs one 60 s timeout and is then skipped.
- **Timeouts while the app is connected.** Sometimes the app keeps its own preview running but its tunnel stops
  answering new connections, so the script reports `timed out` again and again. Disconnect and reconnect the scope in
  the app; the script waits during the reconnect and carries on by itself.
- **One connection.** The script uses the app's connection and opens no remote session of its own. Keep the app
  connected while it runs.
- **Network exposure.** The app listens on all interfaces (`*:4700`, `*:40080`), so while it is connected anyone on
  your local network can reach the scope through it. Turn on the macOS firewall on untrusted networks.
- **Tested with** a Seestar S50 Pro (firmware 9.75) and the Seestar app 3.4.1 for Mac, at a Starlink-connected
  remote observatory. These ports and RPC methods are undocumented and may change with app or firmware updates.

Needs Python 3.9+ (standard library only). Tests:

    python3 -m unittest seestar/test_seestar_download.py

## seestar_relay

Makes a remote Seestar appear as a LAN device to the official Seestar phone app: it answers the app's UDP discovery
(port 4720) with this machine's address and forwards the scope's TCP ports to an upstream.

Through the Seestar app's tunnel on this Mac:

    python3 seestar/seestar_relay.py --via-app

Straight to the scope's address (replace `HOST`):

    python3 seestar/seestar_relay.py --upstream HOST

Add `--ip ADDR` to either to announce a different address to the phone.

- `--via-app`: the upstream is the Seestar app's tunnel on this Mac. The app already listens on 4700, 4800, 4801 and
  4804, so only TCP 80 is forwarded, to `127.0.0.1:40080` (where the app exposes the scope's HTTP server).
- `--upstream HOST`: forwards TCP 4700, 4800, 4801, 4804 and 80 to `HOST`, the scope's address. If the Seestar app on
  this Mac is connected it already holds those ports and the relay exits naming the busy port.
- `--ip ADDR`: the address announced to the phone (default: this machine's LAN address).

Tested on 2026-11-21 in `--via-app` mode (as a prototype) with a Seestar S50 Pro (firmware 9.75) and the iPhone
Seestar app: discovery, control, live view, album and thumbnails worked.

Requirements: the phone is on the same Wi-Fi as the machine running the relay; the iPhone has given the Seestar app the
Local Network permission; binding port 80 works on macOS without root.

Limits:

- `--via-app` still goes through ZWO's Telescope Network, so it is as slow and as unstable as the Mac app itself.
- `--upstream` is untested. It needs the scope's LAN address, e.g. reached through a Tailscale subnet route.
- Anyone on the local network can reach the scope through the relay.

Tests:

    python3 -m unittest seestar/test_seestar_relay.py
