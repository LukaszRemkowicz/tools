#!/usr/bin/env python3
"""Upload files back to a ZWO Seestar's album, e.g. a stack the download removed, so GoTo from the album works again.

The scope takes new files only on its SMB share ("EMMC Images"), which the Seestar app's tunnel does not carry, so
this drives `seestar upload` from github.com/Praxeis/seestar-link over its own connection to the scope. A stack goes
up with its JPG and a thumbnail (NAME_thn.jpg, made from the JPG), the three files the album shows. See README.md.
"""
import argparse, os, re, shutil, subprocess, sys, tempfile, time

import seestar_download

WAIT = 60        # seconds for ZWO to release a closed app's remote session, and between attempts
ATTEMPTS = 5     # the direct link through Starlink comes and goes, so a failed set-up is worth retrying
RETRY_ON = ('hole-punch', 'device busy', 'no creds')
NOISE = re.compile(r'relay ep|ch Z=|0103 device')   # seestar-link's per-channel debug lines
THUMB_PX = 532   # long side of the scope's own thumbnails


def seestar_bin():
    return shutil.which('seestar') or os.path.expanduser('~/go/bin/seestar')


def thumbnail(jpg, out):
    subprocess.run(['sips', '-Z', str(THUMB_PX), jpg, '--out', out], check=True, capture_output=True)


def stage(paths, to, root):
    """Copy the files to root/<album>/ (album: `to`, else the file's folder name); a stack gets its JPG and a thumbnail.
    shortcut: subs and photos get no thumbnail (a raw sub would need debayering), the album then shows a blank tile."""
    albums = set()
    for p in paths:
        files = [os.path.join(p, f) for f in sorted(os.listdir(p))] if os.path.isdir(p) else [p]
        for f in files:
            name = os.path.basename(f)
            if not os.path.isfile(f) or name.startswith('.') or name.endswith(('.part', '_thn.jpg')):
                continue
            album = to or os.path.basename(os.path.dirname(os.path.abspath(f)))
            d = os.path.join(root, album)
            os.makedirs(d, exist_ok=True)
            albums.add(album)
            shutil.copy2(f, d)
            jpg = f[:-4] + '.jpg'
            if name.startswith('Stacked_') and name.endswith('.fit') and os.path.isfile(jpg):
                shutil.copy2(jpg, d)
                thumbnail(jpg, os.path.join(d, name[:-4] + '_thn.jpg'))
    return sorted(albums)


def quit_seestar_app():
    """The scope takes one remote session, and the Seestar app on this Mac would hold it. True if it was running."""
    if subprocess.run(['pgrep', '-x', 'Seestar'], capture_output=True).returncode:
        return False
    subprocess.run(['osascript', '-e', 'quit app "Seestar"'], capture_output=True)
    return True


def upload(binary, src, album):
    """`seestar upload src album`, retried while the connection to the scope fails to set up."""
    for attempt in range(1, ATTEMPTS + 1):
        print('%s uploading to MyWorks/%s, attempt %d/%d' % (time.strftime('%T'), album, attempt, ATTEMPTS),
              flush=True)
        out = []
        with subprocess.Popen([binary, 'upload', '-no-prompt', src, album], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True) as p:
            for line in p.stdout:
                out.append(line)
                if not NOISE.search(line):
                    print('    ' + line.rstrip(), flush=True)
        if p.returncode == 0:
            return True
        if attempt == ATTEMPTS or not any(k in ''.join(out) for k in RETRY_ON):
            return False
        print('    no connection to the scope this time, trying again in %d s' % WAIT, flush=True)
        time.sleep(WAIT)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Upload files back to the Seestar album over seestar-link.')
    ap.add_argument('paths', nargs='+', metavar='PATH', help='files or album folders, e.g. ~/Pictures/Seestar/"NGC 2070"')
    ap.add_argument('--to', metavar='ALBUM', help="album folder on the scope (default: each file's folder name)")
    ap.add_argument('--version', action='version', version='seestar_upload ' + seestar_download.__version__)
    a = ap.parse_args(argv)
    paths = [os.path.expanduser(p) for p in a.paths]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        ap.error('not found: ' + ', '.join(missing))
    binary = seestar_bin()
    if not os.access(binary, os.X_OK):
        ap.error('seestar-link is missing: go install github.com/Praxeis/seestar-link/cmd/seestar@latest, '
                 'then run: seestar login')
    print('seestar_upload %s' % seestar_download.__version__, flush=True)
    with tempfile.TemporaryDirectory() as root:
        albums = stage(paths, a.to, root)
        if not albums:
            ap.error('nothing to upload')
        for album in albums:
            for f in sorted(os.listdir(os.path.join(root, album))):
                print('  MyWorks/%s/%s' % (album, f))
        if quit_seestar_app():
            print('Closed the Seestar app, the scope takes one remote session. Waiting %d s for ZWO to release it.'
                  % WAIT, flush=True)
            time.sleep(WAIT)
        ok = all(upload(binary, os.path.join(root, album), album) for album in albums)
    print('Done. Open the Seestar app to see the files.' if ok else
          'Upload failed. Try again later: the connection to the scope through Starlink comes and goes.')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
