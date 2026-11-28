import contextlib, io, os, stat, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seestar_upload as u

STACK = 'Stacked_2_M 42.fit'


def touch(path, data=b'x'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


class Stage(unittest.TestCase):
    def setUp(self):
        self.thumb, u.thumbnail = u.thumbnail, lambda jpg, out: touch(out, b'thn')   # no sips in tests

    def tearDown(self):
        u.thumbnail = self.thumb

    def test_stack_gets_jpg_and_thumbnail(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as root:
            for n in (STACK, 'Stacked_2_M 42.jpg', 'Stacked_2_M 42_thn.jpg', 'x.fit.part', '.DS_Store'):
                touch(os.path.join(src, 'M 42', n))
            touch(os.path.join(src, 'M 42_sub', 'Light_1.fit'))
            albums = u.stage([os.path.join(src, 'M 42'), os.path.join(src, 'M 42_sub', 'Light_1.fit')], None, root)
            self.assertEqual(albums, ['M 42', 'M 42_sub'])
            self.assertEqual(sorted(os.listdir(os.path.join(root, 'M 42'))),
                             [STACK, 'Stacked_2_M 42.jpg', 'Stacked_2_M 42_thn.jpg'])   # no .part, no old thumbnail
            with open(os.path.join(root, 'M 42', 'Stacked_2_M 42_thn.jpg'), 'rb') as f:
                self.assertEqual(f.read(), b'thn')                                       # made fresh from the JPG
            self.assertEqual(os.listdir(os.path.join(root, 'M 42_sub')), ['Light_1.fit'])
            self.assertEqual(u.stage([os.path.join(src, 'M 42_sub')], 'Other', root), ['Other'])   # --to


class Upload(unittest.TestCase):
    def fake_seestar(self, d, outputs):
        """A `seestar` that prints outputs[n] on its n-th run; exit 0 when the text says done."""
        script = os.path.join(d, 'seestar')
        with open(script, 'w') as f:
            f.write('#!/bin/sh\nn=$(cat "%s/n" 2>/dev/null || echo 0); echo $((n+1)) > "%s/n"\n' % (d, d))
            for i, text in enumerate(outputs):
                f.write('[ "$n" = %d ] && { echo "%s"; %s; }\n' % (i, text, 'exit 0' if 'done' in text else 'exit 1'))
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
        return script

    def run_upload(self, outputs):
        wait, u.WAIT = u.WAIT, 0
        try:
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
                ok = u.upload(self.fake_seestar(d, outputs), d, 'M 42')
                with open(os.path.join(d, 'n')) as f:
                    return ok, int(f.read())
        finally:
            u.WAIT = wait

    def test_retries_while_the_link_does_not_set_up(self):
        self.assertEqual(self.run_upload(['dial: hole-punch: no response from device', 'done. 1 file(s)']), (True, 2))

    def test_gives_up_on_other_errors(self):
        self.assertEqual(self.run_upload(['permission denied', 'done. 1 file(s)']), (False, 1))


if __name__ == '__main__':
    unittest.main()
