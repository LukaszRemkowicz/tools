import json, os, socket, socketserver, sys, threading, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seestar_relay as r

STATE = {'device': {'sn': 'abc', 'product_model': 'Seestar S50 Pro', 'name': 'n'}, 'ap': {'ssid': 'MyScope'}}


class Rpc(socketserver.StreamRequestHandler):
    def handle(self):
        req = json.loads(self.rfile.readline())
        self.wfile.write(json.dumps({'id': req['id'], 'code': 0, 'result': STATE}).encode() + b'\r\n')


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(4096):
            self.request.sendall(data)


def serve(handler):
    srv = socketserver.ThreadingTCPServer(('127.0.0.1', 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Relay(unittest.TestCase):
    def test_port_map(self):
        self.assertEqual(r.port_map(True), {80: ('127.0.0.1', 40080)})
        self.assertEqual(r.port_map(False, 'h'), {p: ('h', p) for p in (4700, 4800, 4801, 4804, 80)})

    def test_discovery(self):
        rpc = serve(Rpc)
        self.addCleanup(rpc.shutdown)
        info = r.device_info(rpc.server_address, '127.0.0.2')   # not the client's 127.0.0.1, which would be ignored
        sock = r.discovery_socket(0)
        self.addCleanup(sock.close)
        threading.Thread(target=r.discovery, args=(sock, info), daemon=True).start()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as c:
            c.settimeout(5)
            c.sendto(b'not json', ('127.0.0.1', sock.getsockname()[1]))
            c.sendto(json.dumps({'id': 7, 'method': 'scan_iscope'}).encode(), ('127.0.0.1', sock.getsockname()[1]))
            reply = json.loads(c.recvfrom(4096)[0])
        self.assertEqual((reply['id'], reply['method'], reply['code']), (7, 'scan_iscope', 0))
        self.assertEqual((reply['result']['ip'], reply['result']['sn'], reply['result']['ssid']),
                         ('127.0.0.2', 'abc', 'MyScope'))

    def test_forward(self):
        echo = serve(Echo)
        self.addCleanup(echo.shutdown)
        srv = r.listener(0)
        self.addCleanup(srv.close)
        threading.Thread(target=r.forward, args=(srv, echo.server_address), daemon=True).start()
        with socket.create_connection(('127.0.0.1', srv.getsockname()[1]), timeout=5) as c:
            c.sendall(b'hello seestar')
            self.assertEqual(c.recv(4096), b'hello seestar')


if __name__ == '__main__':
    unittest.main()
