import http.server, json, os, socketserver, sys, tempfile, threading, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seestar_download as s

FIT = bytes(range(256)) * 400          # 102400 bytes
SUB = b'sub-frame' * 100
FILES = {'/MyWorks/M%2042/Stacked_2_M%2042.fit': FIT, '/MyWorks/M%2042_sub/Light_M%2042_1.fit': SUB}
ALBUMS = {None: {'path': 'MyWorks', 'list': [{'group_name': 'DeepSky', 'files': [
              {'name': 'M 42_sub', 'count': 1, 'type': 0}, {'name': 'M 42', 'count': 1, 'type': 0}]}]},
          'MyWorks/M 42': {'list': [{'files': [{'name': 'Stacked_2_M 42.fit', 'type': 2}]}]},
          'MyWorks/M 42_sub': {'list': [{'files': [{'name': 'Light_M 42_1.fit', 'type': 2}]}]}}


class Rpc(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline()
        if not line:
            return                                                    # tunnel_up() probe: connect and close
        req = json.loads(line)
        self.wfile.write(b'{"Event":"PiStatus","temp":30}\r\n')      # events arrive before the reply
        reply = {'id': req['id'], 'method': req['method'], 'code': 0, 'result': ALBUMS[req.get('params')]}
        self.wfile.write(json.dumps(reply).encode() + b'\r\n')


class Http(http.server.BaseHTTPRequestHandler):
    log = []
    def log_message(self, *a): pass
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
        Http.log = []

    def tearDown(self):
        for srv in (self.rpc, self.http):
            srv.shutdown(); srv.server_close()

    def test_order_resume_and_rerun(self):
        with tempfile.TemporaryDirectory() as dest:
            self.assertEqual(s.main(['--dest', dest]), 0)
            self.assertEqual(open(os.path.join(dest, 'M 42', 'Stacked_2_M 42.fit'), 'rb').read(), FIT)
            self.assertEqual(open(os.path.join(dest, 'M 42_sub', 'Light_M 42_1.fit'), 'rb').read(), SUB)
            self.assertFalse(os.path.exists(os.path.join(dest, 'M 42', 'Stacked_2_M 42.jpg')))   # missing twin
            paths = [p for p, _ in Http.log]
            self.assertLess(paths.index('/MyWorks/M%2042/Stacked_2_M%2042.fit'),
                            paths.index('/MyWorks/M%2042_sub/Light_M%2042_1.fit'))          # results before subs
            self.assertIn(('/MyWorks/M%2042/Stacked_2_M%2042.fit', 'bytes=%d-' % (len(FIT) // 2)), Http.log)
            Http.log = []
            s.main(['--dest', dest])
            self.assertEqual([p for p, _ in Http.log], ['/MyWorks/M%2042/Stacked_2_M%2042.jpg'])  # only the absent jpg


if __name__ == '__main__':
    unittest.main()
