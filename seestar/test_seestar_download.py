import contextlib, http.server, io, json, os, socketserver, sys, tempfile, threading, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seestar_download as s

FIT = bytes(range(256)) * 400          # 102400 bytes
SUB = b'sub-frame' * 100
FILES = {'/MyWorks/M%2042/Stacked_2_M%2042.fit': FIT, '/MyWorks/M%2042_sub/Light_M%2042_1.fit': SUB,
         '/MyWorks/M%2042_sub/Light_M%2042_2.fit': SUB * 2}
ALBUMS = {None: {'path': 'MyWorks', 'list': [{'group_name': 'DeepSky', 'files': [
              {'name': 'M 42_sub', 'count': 1, 'type': 0}, {'name': 'M 42', 'count': 1, 'type': 0}]}]},
          'MyWorks/M 42': {'list': [{'files': [{'name': 'Stacked_2_M 42.fit', 'type': 2}]}]},
          'MyWorks/M 42_sub': {'list': [{'files': [{'name': 'Light_M 42_2.fit', 'type': 2},
                                                   {'name': 'Light_M 42_1.fit', 'type': 2}]}]}}


def read(*path):
    with open(os.path.join(*path), 'rb') as f:
        return f.read()


class Rpc(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline()
        if not line:
            return                                                    # tunnel_up() probe: connect and close
        req = json.loads(line)
        self.wfile.write(b'{"Event":"PiStatus","temp":30}\r\n')      # events arrive before the reply
        if req['method'] == 'delete_image':
            Rpc.deleted += req['params']['name']
            result = 0
        else:
            result = ALBUMS[req.get('params')]
        reply = {'id': req['id'], 'method': req['method'], 'code': 0, 'result': result}
        self.wfile.write(json.dumps(reply).encode() + b'\r\n')


class Http(http.server.BaseHTTPRequestHandler):
    log = []
    def log_message(self, *a): pass
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(FILES[self.path])))
        self.end_headers()
    def do_GET(self):
        rng = self.headers.get('Range')
        Http.log.append((self.path, rng))
        data = FILES.get(self.path)
        if data is None:
            return self.send_error(404)
        start = int(rng[6:-1]) if rng else 0
        self.send_response(206 if rng else 200)
        self.send_header('Content-Length', str(len(data) - start))
        self.end_headers()
        first_full_fit = self.path.endswith('.fit') and 'Stacked' in self.path and not rng
        self.wfile.write(data[start:len(data) // 2] if first_full_fit else data[start:])   # drop mid-file once


class Download(unittest.TestCase):
    def setUp(self):
        self.rpc = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Rpc)
        self.http = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Http)
        for srv in (self.rpc, self.http):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        s.RPC_ADDR = self.rpc.server_address
        s.HTTP_BASE = 'http://127.0.0.1:%d/' % self.http.server_address[1]
        s.RETRY_PAUSE = 0
        Http.log, Rpc.deleted = [], []
        self.banners = []
        s.notify = self.banners.append                                  # no real macOS banners from tests

    def tearDown(self):
        for srv in (self.rpc, self.http):
            srv.shutdown(); srv.server_close()

    def test_order_resume_and_rerun(self):
        with tempfile.TemporaryDirectory() as dest:
            self.assertEqual(s.main(['--dest', dest]), 0)
            self.assertEqual(read(dest, 'M 42', 'Stacked_2_M 42.fit'), FIT)
            self.assertEqual(read(dest, 'M 42_sub', 'Light_M 42_1.fit'), SUB)
            self.assertFalse(os.path.exists(os.path.join(dest, 'M 42', 'Stacked_2_M 42.jpg')))   # missing twin
            paths = [p for p, _ in Http.log]
            self.assertLess(paths.index('/MyWorks/M%2042/Stacked_2_M%2042.fit'),
                            paths.index('/MyWorks/M%2042_sub/Light_M%2042_1.fit'))          # results before subs
            self.assertIn(('/MyWorks/M%2042/Stacked_2_M%2042.fit', 'bytes=%d-' % (len(FIT) // 2)), Http.log)
            Http.log = []
            s.main(['--dest', dest])
            self.assertEqual([p for p, _ in Http.log], ['/MyWorks/M%2042/Stacked_2_M%2042.jpg'])  # only the absent jpg
            self.assertEqual(Rpc.deleted, [])                                   # nothing deleted without --delete

    def test_delete_only_verified_copies(self):
        with tempfile.TemporaryDirectory() as dest:
            os.makedirs(os.path.join(dest, 'M 42_sub'))
            with open(os.path.join(dest, 'M 42_sub', 'Light_M 42_1.fit'), 'wb') as f:
                f.write(b'older copy, wrong size')                              # "already downloaded", but differs
            self.assertEqual(s.main(['--dest', dest, '--delete']), 1)
            self.assertEqual(Rpc.deleted, ['MyWorks/M 42/Stacked_2_M 42.fit',   # not the jpg, not the bad copy
                                           'MyWorks/M 42_sub/Light_M 42_2.fit'])
            paths = [p for p, _ in Http.log]
            self.assertLess(paths.index('/MyWorks/M%2042/Stacked_2_M%2042.jpg'),
                            paths.index('/MyWorks/M%2042/Stacked_2_M%2042.fit'))    # jpg before its FIT goes

    def test_keep_stacks(self):
        with tempfile.TemporaryDirectory() as dest:
            self.assertEqual(s.main(['--dest', dest, '--delete', '--keep-stacks']), 0)
            self.assertEqual(read(dest, 'M 42', 'Stacked_2_M 42.fit'), FIT)               # the stack is downloaded
            self.assertEqual(Rpc.deleted, ['MyWorks/M 42_sub/Light_M 42_2.fit',
                                           'MyWorks/M 42_sub/Light_M 42_1.fit'])          # only the subs go
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                s.main(['--dest', dest, '--keep-stacks'])                                  # needs --delete

    def test_version(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()) as out:
            s.main(['--version'])
        self.assertEqual(out.getvalue().strip(), 'seestar_download 0.2.0')

    def test_banner_when_app_drops_and_returns(self):
        states = iter([False, False, True])                             # app gone, still gone, back
        up, s.tunnel_up, s.WAIT = s.tunnel_up, lambda: next(states), 0
        try:
            s.wait_for_tunnel()
        finally:
            s.tunnel_up, s.WAIT = up, 30
        self.assertEqual(len(self.banners), 2)
        self.assertIn('lost', self.banners[0])
        self.assertIn('again', self.banners[1])


if __name__ == '__main__':
    unittest.main()
