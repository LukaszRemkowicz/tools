# seestar_download

Download photos from a ZWO Seestar through the connection the official Seestar app already has open, for example to
a scope in a remote observatory reached over Telescope Network.

## Why

Over a remote connection the app downloads slowly and starts a file over whenever the link drops, which happens often.
This script resumes every file from the last byte it received and waits for you to reconnect the app.

## Usage

1. Open the Seestar app **on this Mac** and connect to the scope as usual.
2. Run:

       python3 seestar/seestar_download.py

   Files go to `~/Pictures/Seestar/<album folder>/`. Options: `-o DIR` / `--output DIR` (where the photos go),
   `--folder NAME` (only that album folder, repeatable, e.g. `--folder "NGC 292"`).
3. Stop with Ctrl-C at any time. Run the same command again to continue. Finished files are skipped and a half-done
   file continues from its `.part`.

The first lines show the target folder and the mode: `download_only` (default) or `download_and_delete`.

To free the scope's storage as you go, add `--delete` (mode `download_and_delete`). Each file is then deleted from the scope right after its copy
here is complete. Files downloaded in an earlier run are deleted too, but only after a `HEAD` request confirms the copy
here has the scope's exact size. JPG previews of stacks are never deleted by name; the scope may remove them together
with their FIT. Deleting cannot be undone.

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

Needs Python 3.11+ (standard library only). Tests: `python3 -m unittest seestar/test_seestar_download.py`.
