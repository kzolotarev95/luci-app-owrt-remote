"""Exercise both generated reverse channels with real Xray processes on loopback."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
import uuid

import test_host_policy as shell_tests

hub, SH = shell_tests.hub, shell_tests.SH


class HTTPProbe(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'OWRT_HTTP_REVERSE_OK'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SSHProbe(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(b'SSH-2.0-OWRT_TEST\r\n')
        self.request.settimeout(2)
        try:
            self.request.recv(128)
        except OSError:
            pass


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get('OWRT_TEST_XRAY_BIN'), 'Set a real compatible Xray binary')
class ReverseTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owrt-reverse-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.http_server = ThreadingHTTPServer(('127.0.0.1', 0), HTTPProbe)
        self.ssh_server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), SSHProbe)
        self.ssh_server.daemon_threads = True
        for server in (self.http_server, self.ssh_server):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(self.close_server, server, thread)
        self.client_bin = Path(os.environ['OWRT_TEST_XRAY_BIN']).resolve()

    @staticmethod
    def close_server(server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    def check_pair(self, server_bin, shell_client=False, tachyon=False):
        processes, streams = [], []
        row = dict(id='probe', name='probe', entry_port=free_port(), ssh_entry_port=free_port(),
                   vps_host='127.0.0.1', vless_port=free_port(), vless_uuid=str(uuid.uuid4()),
                   ssh_vless_uuid=str(uuid.uuid4()), vless_encryption='none', vless_flow='',
                   reverse_tag='reverse-in', ssh_reverse_tag='reverse-in-ssh',
                   admin_host='127.0.0.1', admin_port=self.http_server.server_port,
                   ssh_host='127.0.0.1', ssh_port=self.ssh_server.server_address[1])
        if tachyon:
            class TachyonProbe(HTTPProbe):
                def do_GET(self):
                    data = b'TACHYON_SELECTED_ROUTER_OK'
                    self.send_response(200); self.send_header('Content-Length', str(len(data)))
                    self.end_headers(); self.wfile.write(data)
            service = ThreadingHTTPServer(('127.0.0.1', 0), TachyonProbe)
            thread = threading.Thread(target=service.serve_forever, daemon=True); thread.start()
            self.addCleanup(self.close_server, service, thread)
            row['tachyon_entry_port'] = free_port()
            row['status_json'] = json.dumps({'tachyon': {'checked': True, 'installed': True,
                'mode': 'standalone', 'port': service.server_port, 'available': True}})
        configs = dict(server=hub.make_server_xray_config([row], listen_host='127.0.0.1',
                                                         listen_port=row['vless_port']),
                       client=hub.make_router_xray_config(row))
        if shell_client:
            harness = shell_tests.RouterShellTests()
            harness.setUp()
            try:
                harness.config(vps_host=row['vps_host'], vps_port=row['vless_port'],
                               vless_uuid=row['vless_uuid'], ssh_vless_uuid=row['ssh_vless_uuid'],
                               reverse_tag=row['reverse_tag'], ssh_reverse_tag=row['ssh_reverse_tag'],
                               admin_host=row['admin_host'], admin_port=row['admin_port'],
                               ssh_host=row['ssh_host'], ssh_port=row['ssh_port'])
                injection = ('tachyon_tunnel_port() { printf ' + str(service.server_port) + '; }\n') if tachyon else ''
                result = harness.run_shell(injection + 'render_client_config --stdout')
                self.assertEqual(result.returncode, 0, result.stderr)
                configs['client'] = json.loads(result.stdout)
            finally:
                harness.doCleanups()
        try:
            for name, config in configs.items():
                config['log'] = {'loglevel': 'debug'}
                path = self.root / (name + '.json')
                path.write_text(json.dumps(config), encoding='utf-8')
                binary = server_bin if name == 'server' else self.client_bin
                check = subprocess.run([str(binary), '-test', '-config', str(path)],
                                       capture_output=True, text=True, timeout=10)
                self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
                stream = (self.root / (name + '.log')).open('wb')
                streams.append(stream)
                processes.append(subprocess.Popen([str(binary), 'run', '-config', str(path)],
                                                   stdout=stream, stderr=subprocess.STDOUT))
            deadline = time.monotonic() + 18
            response, error = None, ''
            while time.monotonic() < deadline:
                connection = http.client.HTTPConnection('127.0.0.1', row['entry_port'], timeout=1)
                try:
                    connection.request('GET', '/')
                    with connection.getresponse() as result:
                        response = result.status, result.read()
                    break
                except OSError as exc:
                    error = repr(exc)
                finally:
                    connection.close()
                if any(process.poll() is not None for process in processes):
                    break
                time.sleep(.1)
            logs = '\n'.join((self.root / (name + '.log')).read_text(encoding='utf-8', errors='replace')[-6000:]
                             for name in configs)
            self.assertEqual(response, (200, b'OWRT_HTTP_REVERSE_OK'), error + '\n' + logs)
            with socket.create_connection(('127.0.0.1', row['ssh_entry_port']), timeout=3) as sock:
                sock.settimeout(3)
                self.assertEqual(sock.recv(128), b'SSH-2.0-OWRT_TEST\r\n', logs)
            if tachyon:
                connection = http.client.HTTPConnection('127.0.0.1', row['tachyon_entry_port'], timeout=5)
                try:
                    connection.request('GET', '/')
                    with connection.getresponse() as result:
                        self.assertEqual((result.status, result.read()), (200, b'TACHYON_SELECTED_ROUTER_OK'), logs)
                finally:
                    connection.close()
        finally:
            for process in processes:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for stream in streams:
                stream.close()

    def test_compatible_router_with_same_server_version(self):
        self.check_pair(self.client_bin)

    @unittest.skipUnless(os.environ.get('OWRT_TEST_NEW_XRAY_BIN'), 'Set a modern server Xray binary')
    def test_compatible_router_with_modern_server(self):
        self.check_pair(Path(os.environ['OWRT_TEST_NEW_XRAY_BIN']).resolve())

    @unittest.skipUnless(SH and os.environ.get('OWRT_TEST_NEW_XRAY_BIN'), 'POSIX shell and modern server required')
    def test_real_router_shell_config_with_modern_server(self):
        self.check_pair(Path(os.environ['OWRT_TEST_NEW_XRAY_BIN']).resolve(), shell_client=True)

    @unittest.skipUnless(SH and os.environ.get('OWRT_TEST_NEW_XRAY_BIN'), 'POSIX shell and modern server required')
    def test_tachyon_admin_and_ssh_share_reverse_without_cross_routing(self):
        self.check_pair(Path(os.environ['OWRT_TEST_NEW_XRAY_BIN']).resolve(), shell_client=True, tachyon=True)

    @unittest.skipUnless(SH and os.environ.get('OWRT_TEST_OLD_XRAY_BIN'), 'Legacy server Xray binary required')
    def test_tachyon_with_26327_server_preserves_destination_port(self):
        self.check_pair(Path(os.environ['OWRT_TEST_OLD_XRAY_BIN']).resolve(), shell_client=True, tachyon=True)

    @unittest.skipUnless(SH and os.environ.get('OWRT_TEST_OLD_XRAY_BIN'), 'Legacy Xray binaries required')
    def test_tachyon_with_26327_router_and_server(self):
        self.client_bin = Path(os.environ['OWRT_TEST_OLD_XRAY_BIN']).resolve()
        self.check_pair(self.client_bin, shell_client=True, tachyon=True)


if __name__ == '__main__':
    unittest.main()
