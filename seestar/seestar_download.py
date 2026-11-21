#!/usr/bin/env python3
"""Download a Seestar's photos through the tunnel that the official Seestar app opens on this Mac.

While the app is connected to the scope (over Telescope Network or Wi-Fi) it serves the scope's ports on this
computer: 127.0.0.1:4700 (JSON-RPC, used for the album list) and 127.0.0.1:40080 (the scope's HTTP file server).
Every file is written to NAME.part first and resumed from the last byte after any interruption. See README.md.
"""
import argparse, collections, enum, errno, http.client, json, os, socket, subprocess, sys, time, urllib.error
import urllib.parse, urllib.request

RPC_ADDR = ('127.0.0.1', 4700)
HTTP_BASE = 'http://127.0.0.1:40080/'
ROOT = 'MyWorks'
WAIT = 30          # seconds between checks while the app is not connected
TIMEOUT = 60       # seconds without data before a request counts as stalled
RETRY_PAUSE = 5    # seconds before retrying a stalled transfer
MAX_STUCK = 5      # stalled attempts in a row without progress (app still connected) before a file is skipped
TRANSIENT = (OSError, http.client.HTTPException)   # URLError/HTTPError, timeouts, resets, IncompleteRead
# Seen in practice: the app stays connected (its own preview keeps flowing) but new connections through its tunnel
# get no answer until the scope is disconnected and reconnected in the app.
HINT = 'if this repeats, disconnect and reconnect the scope in the Seestar app'
COMPLETED, NO_JPG, FAILED = 'COMPLETED', 'SKIPPED (no jpg on the scope)', 'FAILED (stalled, skipped)'


class Mode(enum.StrEnum):
    DOWNLOAD_ONLY = 'download_only'
    DOWNLOAD_AND_DELETE = 'download_and_delete'   # delete each file on the scope once its copy here is complete


def rpc(method, params=None):
    """One JSON-RPC call. The scope may interleave event lines, so the reply is matched by id."""
    req = {'id': 1, 'method': method}
    if params is not None:
        req['params'] = params
    deadline = time.time() + TIMEOUT
    with socket.create_connection(RPC_ADDR, timeout=TIMEOUT) as s:
        s.sendall((json.dumps(req) + '\r\n').encode())
        buf = b''
        while time.time() < deadline:
            chunk = s.recv(65536)
            if not chunk:
                break
            *lines, buf = (buf + chunk).split(b'\r\n')
            for line in lines:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get('id') == 1:
                    if msg.get('code', 0):
                        raise RuntimeError('%s %s: %s' % (method, params or '', msg.get('error')))
                    return msg['result']
    raise TimeoutError('no reply to ' + method)


def tunnel_up():
    """The app listens on 4700 only while it is connected to the scope. Probe by trying to bind that port:
    connecting to it instead would open one more channel in the app's tunnel on every check."""
    with socket.socket() as probe:   # right after the app quits, TIME_WAIT keeps this True for ~30 s; see refused()
        try:
            probe.bind(('', RPC_ADDR[1]))
            return False
        except OSError as e:
            return e.errno == errno.EADDRINUSE


def refused(e):
    """Connection refused means nothing listens, i.e. the app is not connected, whatever tunnel_up() says."""
    return isinstance(e, ConnectionRefusedError) or isinstance(getattr(e, 'reason', None), ConnectionRefusedError)


def notify(text):
    """macOS banner, so a lost connection is noticed away from the terminal. Text must not contain double quotes."""
    if sys.platform == 'darwin':
        subprocess.run(['osascript', '-e', 'display notification "%s" with title "Seestar download"' % text],
                       capture_output=True, check=False)


def wait_for_tunnel(refused_now=False):
    """Return once the app is connected to the scope, with a banner when it is lost and when it is back."""
    if not refused_now and tunnel_up():
        return
    notify('The Seestar app lost the scope. Reconnect it in the app, the download then continues by itself.')
    while True:
        print('Seestar app is not connected to the scope (nothing on %s:%d). Connect it in the app; '
              'checking again in %d s...' % (*RPC_ADDR, WAIT), flush=True)
        time.sleep(WAIT)
        if tunnel_up():
            break
    notify('The Seestar app is connected to the scope again. Download continues.')
    print('Seestar app is connected again, continuing.', flush=True)


def call(method, params=None):
    while True:
        wait_for_tunnel()
        try:
            return rpc(method, params)
        except TRANSIENT as e:
            if refused(e):
                wait_for_tunnel(refused_now=True)
                continue
            print('  %s failed (%s), retrying; %s' % (method, e, HINT), flush=True)
            time.sleep(RETRY_PAUSE)


def plan(only=None):
    """(folder, file, optional) for every album file. Result folders come before the *_sub frame folders."""
    folders = [f['name'] for g in call('get_albums')['list'] for f in g['files'] if not only or f['name'] in only]
    for folder in sorted(folders, key=lambda n: n.endswith('_sub')):   # stable sort keeps the scope's order
        try:
            listing = call('get_albums', '%s/%s' % (ROOT, folder))
        except RuntimeError as e:
            print('  skipping %s: %s' % (folder, e))
            continue
        for g in listing['list']:
            for f in g['files']:
                if f['name'].startswith('Stacked_') and f['name'].endswith('.fit'):
                    # shortcut: the JPG next to a stacked FIT is not listed, so its name is derived from Seestar's
                    # naming; the scope does not answer requests for missing files, so it is skipped after one stall.
                    # It comes before its FIT because deleting the FIT (--delete) may take the JPG with it.
                    yield folder, f['name'][:-4] + '.jpg', True
                yield folder, f['name'], False


def url(folder, name):
    return HTTP_BASE + urllib.parse.quote('%s/%s/%s' % (ROOT, folder, name))


def remote_size(folder, name):
    with urllib.request.urlopen(urllib.request.Request(url(folder, name), method='HEAD'), timeout=TIMEOUT) as r:
        return int(r.headers['Content-Length'])


def delete(folder, name):
    """Delete one file on the scope, as the Seestar app's album does: delete_image {"name": [paths]}."""
    try:
        call('delete_image', {'name': ['%s/%s/%s' % (ROOT, folder, name)]})
        return 'DELETED from the scope'
    except RuntimeError as e:
        return 'NOT deleted from the scope (%s)' % e


def hms(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return '%dh %02dm %02ds' % (h, m, s) if h else '%dm %02ds' % (m, s) if m else '%ds' % s


def progress(done, total, rate):
    """One short status line, rewritten in place; it must fit a narrow terminal or \\r cannot rewrite it."""
    # no ETA: the tunnel swings between stalls and bursts, so any estimate is noise
    line = '%.1f/%.1f MB (%d%%)  %.0f KB/s' % (done / 1e6, total / 1e6, 100 * done / total, rate / 1e3)
    print('\r    %-56s' % line, end='', flush=True)


def download(folder, name, dest):
    """Fetch one file to dest/folder/name, continuing dest/folder/name.part from its current size."""
    out = os.path.join(dest, folder, name)
    part = out + '.part'
    have = os.path.getsize(part) if os.path.exists(part) else 0
    req = urllib.request.Request(url(folder, name), headers={'Range': 'bytes=%d-' % have} if have else {})
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        e.close()
        if e.code == 416:          # .part is not shorter than the file: stale, start over
            os.remove(part)
        raise
    with resp:
        if have and resp.status != 206:
            have = 0               # server ignored Range and sends the whole file
        length = resp.headers.get('Content-Length')
        if not length:             # without it a cut-off file could not be told from a whole one
            raise http.client.HTTPException('no Content-Length from the scope')
        total = have + int(length)
        done = have
        t0 = shown = time.time()
        with open(part, 'ab' if have else 'wb') as fh:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if time.time() - shown > 2:
                    shown = time.time()
                    progress(done, total, (done - have) / (shown - t0))
    size = os.path.getsize(part)
    if size != total:
        raise http.client.IncompleteRead(b'', total - size)
    os.replace(part, out)          # so a file without .part always has the scope's full size


def fetch(folder, name, optional, dest):
    out = os.path.join(dest, folder, name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    size = lambda: os.path.getsize(out + '.part') if os.path.exists(out + '.part') else 0
    stuck = 0
    while True:
        wait_for_tunnel()
        before = size()
        try:
            download(folder, name, dest)
            return COMPLETED
        except TRANSIENT as e:
            if refused(e) or not tunnel_up():
                print('\r    %-56s' % ('app disconnected, will resume at %.1f MB' % (size() / 1e6)), flush=True)
                wait_for_tunnel(refused_now=refused(e))
                continue
            stuck = 0 if size() > before else stuck + 1
            if stuck >= (1 if optional else MAX_STUCK):
                return NO_JPG if optional else FAILED
            print('\r    %-56s' % ('interrupted (%s), resuming at %.1f MB%s'
                                   % (e, size() / 1e6, '; ' + HINT if stuck else '')), flush=True)
            time.sleep(RETRY_PAUSE)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Download Seestar photos through the tunnel of the Seestar app.')
    ap.add_argument('-o', '--output', '--dest', dest='dest', metavar='DIR', default='~/Pictures/Seestar',
                    help='folder for the photos (default: %(default)s)')
    ap.add_argument('--folder', action='append', help='only this album folder, e.g. "NGC 292" (repeatable)')
    ap.add_argument('--delete', dest='mode', action='store_const', const=Mode.DOWNLOAD_AND_DELETE,
                    default=Mode.DOWNLOAD_ONLY, help="mode %s: also delete each file from the scope once the copy "
                    "here has the scope's exact size, including files downloaded earlier; cannot be undone "
                    '(default mode: %s)' % (Mode.DOWNLOAD_AND_DELETE, Mode.DOWNLOAD_ONLY))
    a = ap.parse_args(argv)
    delete_after = a.mode is Mode.DOWNLOAD_AND_DELETE
    dest = os.path.expanduser(a.dest)
    counts = collections.Counter()
    started = time.time()
    try:
        print('Saving photos to: %s' % dest)
        print('Mode: %s' % a.mode)
        print('Albums: %s' % (', '.join(a.folder) if a.folder else 'all'))
        print('Reading the album list from the scope...', flush=True)
        files = list(plan(a.folder))
        here = lambda folder, name: os.path.exists(os.path.join(dest, folder, name))
        print('%d files in the album, %d already downloaded.' % (len(files), sum(here(f, n) for f, n, _ in files)),
              flush=True)
        for i, (folder, name, optional) in enumerate(files, 1):
            print('[%d/%d] %s/%s' % (i, len(files), folder, name), flush=True)
            file_started = time.time()
            earlier = here(folder, name)
            result = COMPLETED + ' (already downloaded)' if earlier else fetch(folder, name, optional, dest)
            counts[COMPLETED if earlier else result] += 1
            timing = '' if earlier else '  [this file %s, total %s]' % (hms(time.time() - file_started),
                                                                         hms(time.time() - started))
            print('\r    %-56s' % (result + timing), flush=True)
            if not delete_after or not result.startswith(COMPLETED):
                continue
            if optional:
                print('    left on the scope: it goes together with its FIT', flush=True)
                continue
            same = True   # a fresh download is renamed only at the scope's full size; an older file is re-checked
            if earlier:
                try:
                    same = remote_size(folder, name) == os.path.getsize(os.path.join(dest, folder, name))
                except TRANSIENT:
                    same = None
            note = delete(folder, name) if same else 'NOT deleted from the scope (%s)' % (
                'size check failed' if same is None else 'size differs from the copy here')
            counts[note if note.startswith('DELETED') else 'NOT deleted from the scope'] += 1
            print('    ' + note, flush=True)
    except KeyboardInterrupt:
        print('\nStopped after %s. Run the same command again to resume.' % hms(time.time() - started))
        return 130
    print('Done in %s: ' % hms(time.time() - started) + ', '.join('%d %s' % (n, r) for r, n in counts.items()) + '.')
    return 1 if counts[FAILED] or counts['NOT deleted from the scope'] else 0


if __name__ == '__main__':
    sys.exit(main())
