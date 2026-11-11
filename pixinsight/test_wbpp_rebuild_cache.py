import collections, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wbpp_rebuild_cache as w

KNOWN = [(1768788200, '2026-01-19T02:03:20.000Z'), (1794424726, '2026-11-11T19:18:46.000Z'),
         (1768788219, '2026-01-19T02:03:38.999Z'), (1794424684, '2026-11-11T19:18:03.999Z'),
         (1794426377, '2026-11-11T19:46:16.999Z')]   # ".999" = PJSR float truncation, ~11% of all seconds
K = {'IC': 'a' * 40, 'SA': 'b' * 40, 'DI': 'c' * 40}
T2020 = 1577836800   # 2020-01-01T00:00:00Z, the LMD used in the synthetic log


class MtimeFormula(unittest.TestCase):
    def test_known_pairs(self):
        for sec, iso in KNOWN:
            self.assertEqual(w.iso_of_mtime(sec * 10**9 + 987654321), iso)   # sub-second part is ignored


class SyntheticLog(unittest.TestCase):
    def test_ic_sa_di(self):
        with tempfile.TemporaryDirectory() as td:
            raw, cal, reg, mst = [os.path.join(td, d) for d in ('raw', 'cal', 'reg', 'master')]
            for d in (raw, cal, reg, mst): os.makedirs(d)
            f = {n: os.path.join(d, n) for d, n in [(raw, 'L_01.fit'), (cal, 'L_01_c.xisf'), (reg, 'L_01_c_r.xisf'),
                                                    (reg, 'L_01_c_r.xdrz'), (reg, 'L_01_c_r.xnml'), (mst, 'master_drizzle.xisf')]}
            for p in f.values():
                open(p, 'w').close(); os.utime(p, (T2020, T2020))   # mtime matches the logged LMD
            log = ['Weighted Batch Preprocessing 3.0.1',
                   'Image Calibration has no cached data for key ' + K['IC'], '------------------',
                   'var IC = new ImageCalibration;', 'IC.targetFrames = [ // enabled, path', '];',
                   'IC.outputDirectory = "%s";' % cal, 'IC.outputExtension = ".xisf";', 'IC.outputPrefix = "";',
                   'IC.outputPostfix = "_c";', '------------------------------------------------------------',
                   'IC outputFile: [%s]' % f['L_01_c.xisf'], 'input: %s' % f['L_01.fit'],
                   '[cache] - Set LMD [%s] L_01.fit: 2020-01-01T00:00:00.000Z' % K['IC'],
                   '[cache] - Set LMD [%s] L_01_c.xisf: 2020-01-01T00:00:00.000Z' % K['IC'],
                   '[cache] - Saved with key: ' + K['IC'],
                   'StarAlignment: no cache data for key ' + K['SA'], 'var SA = new StarAlignment;',
                   'SA.outputDirectory = "%s";' % reg, 'SA.outputExtension = ".xisf";', 'SA.outputPostfix = "_r";',
                   '------------------------------------------------------------', 'Registered frame 1: %s ---> %s' % (f['L_01_c.xisf'], f['L_01_c_r.xisf']),
                   '[cache] - Set LMD [%s] L_01_c.xisf: 2020-01-01T00:00:00.000Z' % K['SA'],
                   '[cache] - Set LMD [%s] L_01_c_r.xisf: 2020-01-01T00:00:00.000Z' % K['SA'],
                   '[cache] - Saved with key: ' + K['SA'],
                   'Drizzle Integration has no cache data for key ' + K['DI'], '------------------------------------------------------------',
                   'var DI = new DrizzleIntegration;', 'DI.inputData = [ // enabled, path, localNormalizationDataPath',
                   '   [true, "%s", "%s"]' % (f['L_01_c_r.xdrz'], f['L_01_c_r.xnml']), '];',
                   '------------------------------------------------------------', 'Writing master Light frame:', f['master_drizzle.xisf'],
                   '[cache] - Saved with key: ' + K['DI']]
            for n in ('master_drizzle.xisf', 'L_01_c_r.xisf', 'L_01_c_r.xdrz', 'L_01_c_r.xnml'):
                log.insert(-1, '[cache] - Set LMD [%s] %s: 2020-01-01T00:00:00.000Z' % (K['DI'], n))
            lines = ['[2020-01-01 00:00:00] ' + l for l in log]
            recs = collections.OrderedDict()
            w.parse_log(lines, recs)
            self.assertEqual({k: r['step'] for k, r in recs.items()}, {K['IC']: 'IC', K['SA']: 'SA', K['DI']: 'DI'})
            catalog = collections.defaultdict(set)
            for p in f.values(): catalog[os.path.basename(p)].add(p)
            w.build(recs, catalog)
            cache, _ = w.assemble(recs, '\n'.join(log))
            self.assertEqual(cache[K['IC']], {f['L_01.fit']: f['L_01_c.xisf']})
            self.assertEqual(cache[K['SA']], {f['L_01_c.xisf']: f['L_01_c_r.xisf']})
            self.assertEqual(cache[K['DI']], f['master_drizzle.xisf'])
            ck = 'LMD_' + K['IC'] + f['L_01.fit']   # LMD comes from the current mtime, not the logged value
            self.assertEqual(cache[ck], w.iso_of_mtime(os.stat(f['L_01.fit']).st_mtime_ns))
            self.assertEqual(cache[f['L_01_c.xisf']][0]['keys'], ['LMD_%s%s' % (K['IC'], f['L_01_c.xisf']), 'LMD_%s%s' % (K['SA'], f['L_01_c.xisf'])])
            # master rewritten after the log (e.g. a later run with other inputs): its stale entry must not be cached
            os.utime(f['master_drizzle.xisf'], (T2020 + 3600, T2020 + 3600))
            # raw frame replaced outside WBPP: keep the logged stamp so WBPP detects the change and recalibrates
            os.utime(f['L_01.fit'], (T2020 + 3600, T2020 + 3600))
            w.build(recs, catalog)
            cache, _ = w.assemble(recs, '\n'.join(log))
            self.assertNotIn(K['DI'], cache)
            self.assertIn(K['SA'], cache)
            self.assertEqual(cache['LMD_' + K['IC'] + f['L_01.fit']], '2020-01-01T00:00:00.000Z')


if __name__ == '__main__':
    unittest.main()
