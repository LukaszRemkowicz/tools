#!/usr/bin/env python3
"""Download a Seestar's photos through the tunnel that the official Seestar app opens on this Mac.

While the app is connected to the scope (over Telescope Network or Wi-Fi) it serves the scope's ports on this
computer: 127.0.0.1:4700 (JSON-RPC, used for the album list) and 127.0.0.1:40080 (the scope's HTTP file server).
Every file is written to NAME.part first and resumed from the last byte after any interruption. See README.md.
"""
import argparse, collections, http.client, json, os, socket, sys, time, urllib.error, urllib.parse, urllib.request

RPC_ADDR = ('127.0.0.1', 4700)
HTTP_BASE = 'http://127.0.0.1:40080/'
ROOT = 'MyWorks'
WAIT = 30          # seconds between checks while the app is not connected
TIMEOUT = 60       # seconds without data before a request counts as stalled
RETRY_PAUSE = 5    # seconds before retrying a stalled transfer
MAX_STUCK = 5      # stalled attempts in a row without progress (app still connected) before a file is skipped
TRANSIENT = (OSError, http.client.HTTPException)   # URLError/HTTPError, timeouts, resets, IncompleteRead


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
    """The app listens on 4700 only while it is connected to the scope."""
    try:
        socket.create_connection(RPC_ADDR, timeout=5).close()
        return True
    except OSError:
        return False


def wait_for_tunnel():
    while not tunnel_up():
        print('Seestar app is not connected to the scope (nothing on %s:%d). Connect it in the app; '
              'checking again in %d s...' % (*RPC_ADDR, WAIT), flush=True)
        time.sleep(WAIT)


def call(method, params=None):
    while True:
        wait_for_tunnel()
        try:
            return rpc(method, params)
        except TRANSIENT as e:
            print('  %s failed (%s), retrying' % (method, e), flush=True)
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
                yield folder, f['name'], False
                if f['name'].startswith('Stacked_') and f['name'].endswith('.fit'):
                    # shortcut: the JPG next to a stacked FIT is not listed, so its name is derived from Seestar's
                    # naming; the scope does not answer requests for missing files, so it is skipped after one stall
                    yield folder, f['name'][:-4] + '.jpg', True


def download(folder, name, dest, label):
    """Fetch one file to dest/folder/name, continuing dest/folder/name.part from its current size."""
    out = os.path.join(dest, folder, name)
    part = out + '.part'
    have = os.path.getsize(part) if os.path.exists(part) else 0
    url = HTTP_BASE + urllib.parse.quote('%s/%s/%s' % (ROOT, folder, name))
    req = urllib.request.Request(url, headers={'Range': 'bytes=%d-' % have} if have else {})
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code == 416:          # .part is not shorter than the file: stale, start over
            os.remove(part)
        raise
    with resp:
        if have and resp.status != 206:
            have = 0               # server ignored Range and sends the whole file
        length = resp.headers.get('Content-Length')
        total = have + int(length) if length else None
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
                    rate = (done - have) / max(shown - t0, 1e-3)
                    print('\r  %s  %.1f/%s MB  %.0f KB/s ' % (label, done / 1e6, '%.1f' % (total / 1e6) if total
                                                               else '?', rate / 1e3), end='', flush=True)
    size = os.path.getsize(part)
    if total is not None and size != total:
        raise http.client.IncompleteRead(b'', total - size)
    os.replace(part, out)


def fetch(folder, name, optional, dest, label):
    out = os.path.join(dest, folder, name)
    if os.path.exists(out):
        return 'have'
    os.makedirs(os.path.dirname(out), exist_ok=True)
    size = lambda: os.path.getsize(out + '.part') if os.path.exists(out + '.part') else 0
    stuck = 0
    while True:
        wait_for_tunnel()
        before = size()
        try:
            download(folder, name, dest, label)
            return 'downloaded'
        except TRANSIENT as e:
            if not tunnel_up():
                print('\n  %s: app disconnected, will resume at %.1f MB' % (label, size() / 1e6), flush=True)
                continue
            stuck = 0 if size() > before else stuck + 1
            if stuck >= (1 if optional else MAX_STUCK):
                return 'no jpg' if optional else 'failed'
            print('\n  %s: interrupted (%s), resuming at %.1f MB' % (label, e, size() / 1e6), flush=True)
            time.sleep(RETRY_PAUSE)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Download Seestar photos through the tunnel of the Seestar app.')
    ap.add_argument('--dest', default='~/Pictures/Seestar', help='target folder (default: %(default)s)')
    ap.add_argument('--folder', action='append', help='only this album folder, e.g. "NGC 292" (repeatable)')
    a = ap.parse_args(argv)
    dest = os.path.expanduser(a.dest)
    counts = collections.Counter()
    try:
        files = list(plan(a.folder))
        print('%d files in the album, saving to %s' % (len(files), dest), flush=True)
        for i, (folder, name, optional) in enumerate(files, 1):
            label = '[%d/%d] %s/%s' % (i, len(files), folder, name)
            result = fetch(folder, name, optional, dest, label)
            counts[result] += 1
            if result != 'have':
                print('\r  %s: %s%s' % (label, result, ' ' * 20), flush=True)
    except KeyboardInterrupt:
        print('\nStopped. Run the same command again to resume.')
        return 130
    print('Done: %d downloaded, %d already here, %d without jpg, %d failed.'
          % (counts['downloaded'], counts['have'], counts['no jpg'], counts['failed']))
    return 1 if counts['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
