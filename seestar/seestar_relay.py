#!/usr/bin/env python3
"""Make a remote ZWO Seestar appear as a LAN device to the official Seestar phone app.

Answers the app's UDP discovery (scan_iscope on port 4720) with this machine's address and forwards the scope's TCP
ports to an upstream: the Seestar app's tunnel on this Mac (--via-app) or a reachable scope address (--upstream).
See README.md.
"""
import argparse, json, socket, sys, threading, time

import seestar_download

DISCOVERY_PORT = 4720
SCOPE_PORTS = (4700, 4800, 4801, 4804, 80)
RETRY = 10   # seconds between attempts to read the scope's device info


def port_map(via_app, upstream=None):
    """{listen_port: (host, port)}. The app already listens on 4700/4800/4801/4804 and exposes the scope's HTTP as 40080."""
    if via_app:
        return {80: ('127.0.0.1', 40080)}
    return {p: (upstream, p) for p in SCOPE_PORTS}


def local_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(('8.8.8.8', 80))   # UDP connect only picks the route, nothing is sent
        return s.getsockname()[0]


def log(text):
    print('%s %s' % (time.strftime('%T'), text), flush=True)


def device_info(rpc_addr, ip):
    """INFO for the discovery reply, from the scope's get_device_state; retried until the scope answers."""
    seestar_download.RPC_ADDR = rpc_addr
    while True:
        try:
            r = seestar_download.rpc('get_device_state')
            return {'product_model': r['device']['product_model'], 'sn': r['device']['sn'],
                    'ssid': r.get('ap', {}).get('ssid') or r['device']['name'], 'ip': ip, 'is_verified': True,
                    'tcp_client_num': 0, 'can_star_mode_sel_cam': True, 'serc': 'WPA-PSK'}
        except (OSError, RuntimeError, KeyError, TypeError) as e:
            log('cannot read the device info from %s:%d (%s), retrying in %d s...' % (*rpc_addr, e, RETRY))
            time.sleep(RETRY)


def discovery(sock, info):
    """Answer scan_iscope requests on a bound UDP socket, unicast and broadcast (as the real scope does)."""
    port, seen = sock.getsockname()[1], set()
    while True:
        data, addr = sock.recvfrom(4096)
        try:
            req = json.loads(data)
        except ValueError:
            continue
        if not isinstance(req, dict) or req.get('method') != 'scan_iscope' or 'result' in req \
                or addr[0] == info['ip']:   # the last one is our own broadcast echo
            continue
        if addr[0] not in seen:
            seen.add(addr[0])
            log('phone %s found the scope' % addr[0])
        reply = json.dumps({'jsonrpc': '2.0', 'Timestamp': '0', 'method': 'scan_iscope', 'result': info,
                            'code': 0, 'id': req.get('id', 201)}).encode() + b'\r\n'
        for dest in (addr, ('255.255.255.255', port)):
            try:
                sock.sendto(reply, dest)
            except OSError:
                pass   # e.g. no broadcast route; the unicast reply is the one that matters


def discovery_socket(port=DISCOVERY_PORT):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(('', port))
    return s


def pipe(src, dst):
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client, addr, target, port, seen):
    try:
        upstream = socket.create_connection(target, timeout=30)
    except OSError as e:
        log('%s: upstream %s:%d failed: %s' % (addr[0], *target, e))
        client.close()
        return
    upstream.settimeout(None)
    if port != 80 or addr[0] not in seen:   # the phone opens many port 80 connections for thumbnails
        seen.add(addr[0])
        log('%s:%d -> %s:%d' % (addr[0], port, *target))
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    pipe(upstream, client)
    client.close()
    upstream.close()


def listener(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('', port))
    s.listen(64)
    return s


def forward(srv, target):
    """Accept on a listening socket and forward every connection to target."""
    port, seen = srv.getsockname()[1], set()
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle, args=(c, a, target, port, seen), daemon=True).start()


def main(argv=None):
    ap = argparse.ArgumentParser(description='Make a remote Seestar appear as a LAN device to the Seestar phone app.')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--via-app', action='store_true',
                   help="upstream is the Seestar app's tunnel on this Mac (forwards only TCP 80)")
    g.add_argument('--upstream', metavar='HOST', help="forward TCP %s to this scope address" % ', '.join(map(str, SCOPE_PORTS)))
    ap.add_argument('--ip', metavar='ADDR', help='address announced to the phone (default: this machine\'s LAN address)')
    ap.add_argument('--version', action='version', version='seestar_relay ' + seestar_download.__version__)
    a = ap.parse_args(argv)
    try:
        ip = a.ip or local_ip()
        ports = port_map(a.via_app, a.upstream)
        rpc_addr = ('127.0.0.1' if a.via_app else a.upstream, 4700)
        print('Mode: %s' % ('via-app' if a.via_app else 'upstream'))
        print('Upstream: %s' % ('the Seestar app on this Mac (127.0.0.1)' if a.via_app else a.upstream))
        print('Announced IP: %s' % ip, flush=True)
        servers = []
        for port, target in ports.items():
            try:
                servers.append((listener(port), target))
            except OSError as e:
                print('Cannot listen on port %d: %s%s' % (port, e, '' if a.via_app else
                      ' (the Seestar app on this Mac may be connected and holding it)'), file=sys.stderr, flush=True)
                return 1
        info = device_info(rpc_addr, ip)
        log('scope %s (%s), ssid %s' % (info['product_model'], info['sn'], info['ssid']))
        for srv, target in servers:
            threading.Thread(target=forward, args=(srv, target), daemon=True).start()
            log('forwarding :%d -> %s:%d' % (srv.getsockname()[1], *target))
        sock = discovery_socket()
        log('answering discovery on UDP %d' % DISCOVERY_PORT)
        discovery(sock, info)
    except KeyboardInterrupt:
        print('\nStopped.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
