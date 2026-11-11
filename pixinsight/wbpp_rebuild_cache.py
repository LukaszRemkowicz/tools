#!/usr/bin/env python3
"""Rebuild the PixInsight WBPP 3.0.x execution cache from a WBPP output directory (logs + files on disk).

Default is a dry run (parse, build, self-check, print a summary). See README.md.
"""
import argparse, base64, collections, datetime, glob, json, math, os, re, shutil, subprocess, sys, tempfile, time

STEP_OF = {'ImageCalibration': 'IC', 'Debayer': 'DB', 'StarAlignment': 'SA', 'LocalNormalization': 'LN',
           'ImageIntegration': 'II', 'DrizzleIntegration': 'DI'}
RX_LINE = re.compile(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] ?(.*)$')
RX_LMD = re.compile(r'^\[cache\] - Set LMD \[(\w{40})\] (.*): (\d{4}-\d\d-\d\dT[\d:.]+Z)$')
RX_SAVE = re.compile(r'^\[cache\] - Saved with key: (\w{40})')
RX_PARAM = re.compile(r'^(\w+)\.(\w+) = (.*);$')
RX_ITEM = re.compile(r'^\s+\[(?:true|false),')
RX_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
RX_SETTINGS = re.compile(rb'(<v k="runningConfiguration" t="s" c="Zstd\+sh"><s u=")(\d+)(">)([A-Za-z0-9+/=]+)(</s></v>)')
bn, stem = os.path.basename, lambda p: os.path.splitext(bn(p))[0]


def iso_of_mtime(mtime_ns):
    """mtime -> string identical to PJSR FileInfo.lastModified.toISOString() (verified on 3000+ samples)."""
    days, r = divmod(mtime_ns // 1_000_000_000, 86400)   # PJSR drops sub-second part
    jdi = 2440587 + days
    jdf = r / 86400.0 + 0.5                               # JD kept as int + float64 fraction
    if jdf >= 1: jdi += 1; jdf -= 1
    ms = math.trunc(((jdi - 2440587.5) + jdf) * 86400000.0)   # Date ms truncation: ~11% of values give "SS-1.999"
    return datetime.datetime.fromtimestamp(ms // 1000, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + '.%03dZ' % (ms % 1000)


def dt(s): return datetime.datetime.strptime(s[:10] + ' ' + s[11:19], '%Y-%m-%d %H:%M:%S')


def read_log(path, force):
    lines = open(path, errors='replace').read().split('\n')
    m = next((re.search(r'Weighted Batch Preprocessing (\d+\.\d+\.\d+)', l) for l in lines[:40]
              if 'Weighted Batch Preprocessing' in l), None)
    if not m: return None
    if not m.group(1).startswith('3.0.') and not force:
        sys.exit('%s: WBPP %s not supported (3.0.x only); use --force-version to override' % (path, m.group(1)))
    return lines


def parse_log(lines, recs):
    """Collect per-key records (step, process block params/items, LMD rows). Later logs override/extend earlier ones."""
    blocks, saves, keyline, rows, nimg = [], [], {}, [], []
    cur = None
    for i, raw in enumerate(lines):
        m = RX_LINE.match(raw)
        if not m: continue
        ts, msg = m.groups()
        mb = re.match(r'^var \w+ = new (\w+);$', msg)
        if mb:
            cur = dict(i=i, cls=mb.group(1), ts=ts, params={}, items=[]); blocks.append(cur); continue
        if cur:
            if msg.startswith('-----'): cur = None
            elif RX_ITEM.match(msg): cur['items'].append(RX_QUOTED.findall(msg))
            elif RX_PARAM.match(msg):
                _, k, v = RX_PARAM.match(msg).groups()
                cur['params'][k] = v[1:-1] if v.startswith('"') else v
            continue
        mk = re.search(r'for key (\w{40})', msg)
        if mk: keyline.setdefault(mk.group(1), (i, msg))
        ms = RX_SAVE.match(msg)
        if ms: saves.append((i, ms.group(1)))
        ml = RX_LMD.match(msg)
        if ml: rows.append((ml.group(1), ml.group(2), ml.group(3), ts))
        mn = re.match(r'^numberOfImages: (\d+)$', msg)
        if mn: nimg.append((i, int(mn.group(1))))
    here = {}
    for si, key in saves:
        if key in here or key not in keyline: continue
        ki, kmsg = keyline[key]
        prev = max([s for s, _ in saves if s < si] + [-1])
        if ki < prev: continue
        b = next((b for b in reversed(blocks) if prev < b['i'] < si), None)   # last process block of this key's step
        step = STEP_OF.get(b['cls']) if b else None
        if step == 'LN' and 'Reference' in kmsg: step = 'LNref'
        here[key] = dict(step=step, cls=b['cls'] if b else None, block=b,
                         nlog=next((n for j, n in nimg if si < j < si + 15), None))
    for key, name, iso, ts in rows:
        if key not in here: continue
        r = recs.setdefault(key, dict(step=None, cls=None, params={}, items=[], rows={}, ts=None, nlog=None, drop=0))
        r['rows'][name] = (iso, ts)
    for key, h in here.items():
        r = recs.setdefault(key, dict(step=None, cls=None, params={}, items=[], rows={}, ts=None, nlog=None, drop=0))
        if h['block']:   # a log without the process block (cache hit) never overrides one that has it
            r.update(step=h['step'], cls=h['cls'], ts=h['block']['ts'], nlog=h['nlog'])
            r['params'] = h['block']['params']
            r['items'] += [q for q in h['block']['items'] if q not in r['items']]
        elif r['cls'] is None: r['step'] = None


def xisf_nimg(path):
    with open(path, 'rb') as f:
        n = int.from_bytes(f.read(16)[8:12], 'little')
        v = re.findall(r'numberOfImages&quot; value=&quot;(\d+)', f.read(n).decode('utf8', 'replace'))
    return int(v[0]) if v else None


def build(recs, catalog):
    """Fill r['value'] (cache data entry or None) and r['paths'] (LMD basename -> full path) for every key.
    Never invents anything: uncertain/missing entries are dropped and counted in r['drop']."""
    exists = os.path.exists
    for r in recs.values():
        step, P, rows = r['step'], r['params'], r['rows']
        r.update(value=None, paths={}, drop=0)
        if step is None: continue
        items = [q for q in r['items'] if q]
        scope = {s for q in items for s in q if s.startswith('/')} | {v for v in P.values() if v.startswith('/')}
        r['initems'] = scope | {os.path.splitext(x)[0] + '.xisf' for x in scope if x.endswith('.xdrz')}   # DI lists xdrz only
        pairs = {}
        if step in ('IC', 'DB', 'SA', 'LN', 'LNref'):
            # inputs are not listed in the IC/SA/LN blocks: pair LMD basenames by the step's output naming rule
            for n in rows:
                if step in ('LN', 'LNref'): on = stem(n) + '.xnml'
                else: on = P.get('outputPrefix', '') + stem(n) + P.get('outputPostfix', '') + P.get('outputExtension', '')
                if on != n and on in rows: pairs[n] = on
            if step == 'DB' and not (P.get('outputRGBImages') == 'true' and P.get('outputSeparateChannels') == 'false'):
                pairs, r['note'] = {}, 'only COMBINED Debayer output supported'

        def res(n):   # basename -> full path: block scope first, then the global catalog; ambiguous/missing -> None
            c = {p for p in scope if bn(p) == n and exists(p)} or {p for p in catalog.get(n, ()) if exists(p)}
            return c.pop() if len(c) == 1 else None
        outs = {}   # input basename -> (input path, output path); empty outputDirectory = next to the input
        for n, on in pairs.items():
            p = res(n)
            if p: outs[n] = (p, os.path.join(P.get('outputDirectory') or os.path.dirname(p), on)); scope.add(outs[n][1])
        r['paths'] = {n: res(n) for n in rows}
        r['drop'] += sum(1 for p in r['paths'].values() if p is None)
        # output rewritten after this log (e.g. by a later run with other inputs) -> its old entry would lie; drop it
        fresh = lambda o: iso_of_mtime(os.stat(o).st_mtime_ns) == rows[bn(o)][0]
        d = {}
        for n, (p, o) in outs.items():
            if exists(o) and r['paths'].get(pairs[n]) == o and fresh(o): d[p] = {'': o} if step == 'DB' else o
            else: r['drop'] += 1
        r['value'] = d or None
        if step in ('II', 'DI'):
            masters = {p for p in r['paths'].values() if p and p not in r['initems'] and p.endswith('.xisf')}
            if len(masters) != 1: r['note'] = 'output master not unique/found'; continue
            m = masters.pop()
            if not fresh(m): r['note'] = 'output rewritten after this log'; continue
            if step == 'DI': r['value'] = m; continue
            n, nl = xisf_nimg(m), r['nlog']
            ok = n is not None and (nl is None or nl == n)
            dr = [q[1] for q in items if len(q) > 1 and q[1]]
            if ok and dr:   # failedFrames: drizzle files NOT rewritten by II (mtime before II start) -> cannot name them safely
                iso, ts = rows[bn(m)]
                off = round((dt(ts) - dt(iso)).total_seconds() / 900) * 900   # log local time vs UTC, whole quarter hours
                t0 = dt(r['ts']) - datetime.timedelta(seconds=off)
                ok = all(bn(x) in rows and dt(rows[bn(x)][0]) >= t0 for x in dr)
            if ok: r['value'] = {'IICacheOutputFilePath': m, 'numberOfImages': n, 'failedFrames': []}
            else: r['note'] = 'numberOfImages/failedFrames undetermined'


def assemble(recs, logtext):
    cache, hist, stats = {}, collections.OrderedDict(), []
    last = {}   # path -> newest LMD WBPP itself logged for it (any step, any log)
    for r in recs.values():
        for n, p in r.get('paths', {}).items():
            if p: last[p] = max(last.get(p, ''), r['rows'][n][0])
    for key, r in recs.items():
        if r['step'] is None or r['value'] is None:
            stats.append((key, r['step'] and r['step'], 0, 0, r['drop'], r.get('note') or 'skipped (will be recomputed)'))
            r['skip'] = True
            continue
        cache[key] = r['value']
        extra = []
        if r['step'] == 'LNref' and r['params'].get('referenceIsView') == 'false':   # WBPP checks this LMD but never stores it
            ref = r['params'].get('referencePathOrViewId', '')
            if os.path.exists(ref) and bn(ref) not in r['rows']: extra = [(bn(ref), ref)]
        lmd = 0
        for n, p in [(n, p) for n, p in r['paths'].items() if p] + extra:
            cur = iso_of_mtime(os.stat(p).st_mtime_ns)
            if cur != last.get(p):   # changed outside WBPP's logged writes: keep the old stamp so WBPP sees the change
                if n not in r['rows']: continue
                cur = r['rows'][n][0]
            ck = 'LMD_' + key + p
            cache[ck] = cur; lmd += 1
            h = hist.setdefault(p, {'lmd': cur, 'keys': []})
            if ck not in h['keys']: h['keys'].append(ck)
            if n in r['rows'] and abs(dt(cur).timestamp() - dt(r['rows'][n][0]).timestamp()) <= 1:
                assert r['rows'][n][0] == cur, 'mtime->ISO formula mismatch for ' + p
        label = r['step']
        if r['step'] == 'II': label += ' ' + bn(r['value']['IICacheOutputFilePath'])
        stats.append((key, label, len(r['value']) if isinstance(r['value'], dict) and 'IICacheOutputFilePath' not in r['value'] else 1,
                      lmd, r['drop'], ''))
    for p, h in hist.items():
        assert p not in cache; cache[p] = [h]
    # self-checks
    keys = {k for k, r in recs.items() if not r.get('skip')}
    def walk(v):
        if isinstance(v, str): yield v
        elif isinstance(v, dict):
            for k, x in v.items():
                if k.startswith('/'): yield k
                yield from walk(x)
    for k in keys:
        for p in walk(cache[k]):
            if p.startswith('/'):
                assert os.path.exists(p) and p in logtext, 'data path missing or not in logs: ' + p
    for ck in [c for c in cache if c.startswith('LMD_')]:
        assert os.path.exists(ck[44:]) and ck[4:44] in keys, ck
    assert json.loads(json.dumps(cache)) == cache
    return cache, stats


def zstd(args, data):
    with tempfile.TemporaryDirectory() as td:
        i, o = os.path.join(td, 'i'), os.path.join(td, 'o')
        open(i, 'wb').write(data)
        subprocess.run(['zstd', '-q', '-f'] + args + [i, '-o', o], check=True)
        return open(o, 'rb').read()


def patch_settings(path, out_dir):
    """Drop "cache_v2" from the saved runningConfiguration (else WBPP's 'reload previous configuration' replaces our cache).
    Only if that configuration was made for out_dir. Returns a status message; writes the file only on change."""
    src = open(path, 'rb').read()
    ms = RX_SETTINGS.findall(src)
    if len(ms) != 1: return 'settings: no (single, Zstd+sh) runningConfiguration found, not patched'
    m = RX_SETTINGS.search(src)
    raw = zstd(['-d'], base64.b64decode(m.group(4)))
    h = len(raw) // 2
    sh = bytearray(len(raw)); sh[0::2] = raw[:h]; sh[1::2] = raw[h:]     # undo byte-shuffle (itemsize 2)
    old = bytes(sh).decode('utf-16le')
    conf = json.loads(old)['data']
    cfg_dir = base64.b64decode(conf.get('outputDirectory', '')).decode('utf8', 'replace')
    if os.path.normpath(cfg_dir) != out_dir: return 'settings: runningConfiguration is for a different output directory, not patched'
    if 'cache_v2' not in conf: return 'settings: runningConfiguration has no cache_v2, nothing to patch'
    new, n = re.subn(r'\n    "cache_v2": "[^"\n]*",?(?=\n)', '', old)
    new = re.sub(r',(\n  \}\n\}$)', r'\1', new)
    assert n == 1 and json.loads(new)['data'] == {k: v for k, v in conf.items() if k != 'cache_v2'}
    b = new.encode('utf-16le')
    enc = base64.b64encode(zstd(['-3', '--no-check'], b[0::2] + b[1::2]))
    tmp = path + '.tmp'
    open(tmp, 'wb').write(src[:m.start()] + m.group(1) + str(len(b)).encode() + m.group(3) + enc + m.group(5) + src[m.end():])
    os.replace(tmp, path)
    return 'settings: removed cache_v2 from runningConfiguration'


def pixinsight_running():
    for args in (['-x', 'PixInsight'], ['-f', 'PixInsight.app/Contents/MacOS/PixInsight']):
        try: r = subprocess.run(['pgrep'] + args, capture_output=True)
        except FileNotFoundError: sys.exit('pgrep not found: cannot verify PixInsight is closed, refusing to install')
        if r.returncode == 0: return True
    return False


def install(cache, a, out_dir):
    cfg = a.config_dir or (os.path.expanduser('~/Library/PixInsight') if sys.platform == 'darwin' else None)
    if not cfg: sys.exit('--config-dir is required on this platform')
    nnn = '%03d' % a.instance
    if pixinsight_running(): sys.exit('PixInsight is running: close it first (it would overwrite the cache on exit)')
    patch = not a.no_settings_patch
    if patch and not shutil.which('zstd'): sys.exit('zstd CLI not found (needed to patch settings); install it or use --no-settings-patch')
    cf, sf = [os.path.join(cfg, n % nnn) for n in ('WeightedBatchPreprocessing-%s-pxi.cache', 'core-%s-pxi.settings')]
    bk = os.path.join(cfg, 'backup-' + time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(bk)
    for f in (cf, sf):
        if os.path.exists(f): shutil.copy2(f, bk)
    open(cf + '.tmp', 'w', encoding='utf-8').write(json.dumps(cache, indent=2, ensure_ascii=False))
    os.replace(cf + '.tmp', cf)
    print('installed cache:', cf)
    print(patch_settings(sf, out_dir) if patch and os.path.exists(sf) else 'settings: not patched')
    print('restore: cp "%s"/* "%s"/' % (bk, cfg))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('output_dir'); ap.add_argument('--log', action='append', default=[], help='log file (repeatable); default all WBPP 3.0.x logs in OUTPUT_DIR/logs')
    ap.add_argument('--out', help='write the rebuilt cache JSON here (dry run otherwise)')
    ap.add_argument('--install', action='store_true'); ap.add_argument('--config-dir'); ap.add_argument('--instance', type=int, default=1)
    ap.add_argument('--no-settings-patch', action='store_true'); ap.add_argument('--force-version', action='store_true')
    a = ap.parse_args()
    out_dir = os.path.normpath(os.path.abspath(a.output_dir))
    explicit = bool(a.log)
    paths = a.log or sorted(glob.glob(os.path.join(out_dir, 'logs', '*.log')))   # names are timestamps: oldest -> newest
    recs, texts = collections.OrderedDict(), []
    for p in paths:
        lines = read_log(p, a.force_version)
        if lines is None:
            if explicit: sys.exit(p + ': not a WBPP log')
            continue
        parse_log(lines, recs); texts.append('\n'.join(lines))
        print('log:', os.path.basename(p))
    if not texts: sys.exit('no WBPP 3.0.x logs found')
    logtext = '\n'.join(texts)
    catalog = collections.defaultdict(set)
    for root, _, files in os.walk(out_dir):
        if root != os.path.join(out_dir, 'logs'):
            for f in files: catalog[f].add(os.path.join(root, f))
    for p in re.findall(r'(/[^"\[\]\n]*?\.(?:fit|fits|xisf|xdrz|xnml))(?=[\s"\]<]|$)', logtext):
        if os.path.exists(p): catalog[bn(p)].add(p)
    build(recs, catalog)
    try: cache, stats = assemble(recs, logtext)
    except AssertionError as e: sys.exit('self-check failed: %s' % e)
    print('%-7s %-40s %5s %6s %7s' % ('key', 'step', 'data', 'LMD', 'dropped'))
    for key, label, nd, nl, drop, note in stats:
        print('%-7s %-40s %5s %6s %7s %s' % (key[:6], label or recs[key]['cls'] or 'no process block', nd or '-', nl or '-', drop, note))
    print('cache: %d top-level entries' % len(cache))
    if a.out: open(a.out, 'w', encoding='utf-8').write(json.dumps(cache, indent=2, ensure_ascii=False)); print('written', a.out)
    if a.install: install(cache, a, out_dir)
    elif not a.out: print('dry run (use --out FILE or --install)')


if __name__ == '__main__':
    main()
