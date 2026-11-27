#!/usr/bin/env python3
"""Run real ash/jsonfilter/TLS/background update transactions in a disposable VM.

No user router, VPS or release package is touched. HTTPS is served by a local
test CA and curl's test-only transport shim connects GitHub hosts to that server.
"""
import argparse
import hashlib
import http.server
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tarfile
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fixture', ROOT / 'tests/test_router_panel_update.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--openssl', default='openssl')
    args = parser.parse_args()
    output = ROOT / '.test-output/router-panel-update-vm'
    output.mkdir(parents=True, exist_ok=True)
    case = fixture.RouterPanelUpdateTests()
    case.setUp()
    vm = None
    server = None
    mode = {'api_fail': False, 'corrupt': False, 'delay': 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/repos/kzolotarev95/luci-app-owrt-remote/commits/main':
                time.sleep(mode['delay'])
                if mode['api_fail']:
                    self.send_error(503); return
                data = json.dumps({'sha': fixture.COMMIT}).encode()
            elif self.path == '/kzolotarev95/luci-app-owrt-remote/' + fixture.COMMIT + '/router-update.manifest':
                data = (case.source / 'manifest').read_bytes()
            elif self.path.startswith('/kzolotarev95/luci-app-owrt-remote/' + fixture.COMMIT + '/files/'):
                name = self.path.split('/files/', 1)[1]
                if name not in fixture.manifest.FILES:
                    self.send_error(404); return
                data = b'corrupted' if mode['corrupt'] and name.endswith('/wan.sh') else (case.source / name).read_bytes()
            elif self.path == '/fixture.tar':
                data = archive.read_bytes()
            elif self.path == '/cert.pem':
                data = cert.read_bytes()
            else:
                self.send_error(404); return
            self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    with tempfile.TemporaryDirectory(prefix='owrt-panel-vm-') as directory:
        work = Path(directory)
        try:
            cert, key = work / 'cert.pem', work / 'key.pem'
            subprocess.run([args.openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-keyout', str(key), '-out', str(cert), '-subj', '/CN=api.github.com',
                            '-addext', 'subjectAltName=DNS:api.github.com,DNS:raw.githubusercontent.com'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            archive = work / 'fixture.tar'
            with tarfile.open(archive, 'w') as tar:
                tar.add(case.root, arcname='router-fixture')
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            plain = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            threading.Thread(target=plain.serve_forever, daemon=True).start()
            disk = work / 'router.img'
            shutil.copyfile(args.image, disk)
            port = free_port()
            ssh = ['ssh', '-p', str(port), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=3',
                   '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=' + str(output / 'known_hosts'), 'root@127.0.0.1']
            log = (output / 'transactions.log').open('w', encoding='utf-8')

            def remote(command, check=True, timeout=120):
                result = subprocess.run(ssh + [command], capture_output=True, timeout=timeout)
                text = (result.stdout + result.stderr).decode('utf-8', 'replace')
                log.write(text + '\n'); log.flush()
                if check and result.returncode:
                    raise AssertionError('VM command rc=' + str(result.returncode) + ': ' + command + '\n' + text)
                return result.stdout.decode('utf-8', 'replace').strip()

            qlog = (output / 'qemu.log').open('wb')
            vm = subprocess.Popen([args.qemu, '-display', 'none', '-monitor', 'none', '-serial', 'file:' + str(output / 'console.log'),
                                   '-accel', 'tcg', '-m', '256', '-smp', '1', '-no-reboot', '-drive', 'file=' + str(disk) + ',format=raw',
                                   '-device', 'virtio-net-pci,netdev=lan', '-netdev',
                                   f'user,id=lan,net=192.168.1.0/24,host=192.168.1.2,dhcpstart=192.168.1.100,hostfwd=tcp:127.0.0.1:{port}-192.168.1.1:22'],
                                  stdin=subprocess.DEVNULL, stdout=qlog, stderr=qlog)
            print('Waiting for disposable OpenWrt VM…', flush=True)
            for _ in range(45):
                if vm.poll() is not None:
                    raise AssertionError('QEMU exited')
                if remote('true', check=False, timeout=8) == '' and subprocess.run(ssh + ['true'], capture_output=True, timeout=8).returncode == 0:
                    break
                time.sleep(2)
            else:
                raise AssertionError('VM boot timed out')
            remote('uci set network.lan.gateway=192.168.1.2; uci set network.lan.dns=192.168.1.3; uci commit network; /etc/init.d/network reload; sleep 2')
            print('Preparing curl and real jsonfilter…', flush=True)
            remote('if ! command -v curl >/dev/null || ! command -v jsonfilter >/dev/null; then if command -v apk >/dev/null; then apk update && apk add curl jsonfilter; else opkg update && opkg install curl jsonfilter; fi; fi', timeout=240)
            origin = 'http://192.168.1.2:' + str(plain.server_port)
            remote(f"set -e; wget -qO /tmp/fixture.tar '{origin}/fixture.tar'; tar -xf /tmp/fixture.tar -C /tmp; wget -qO /tmp/update-cert.pem '{origin}/cert.pem'; mkdir /tmp/update-bin")
            wrapper = '#!/bin/sh\nexec /usr/bin/curl --cacert /tmp/update-cert.pem --connect-to api.github.com:443:192.168.1.2:' + str(server.server_port) + ' --connect-to raw.githubusercontent.com:443:192.168.1.2:' + str(server.server_port) + ' "$@"\n'
            import shlex
            remote('printf %s ' + shlex.quote(wrapper) + ' >/tmp/update-bin/curl; chmod 755 /tmp/update-bin/curl')
            env = 'OWRT_PANEL_ROOT=/tmp/router-fixture PATH=/tmp/update-bin:$PATH '
            helper = 'sh /tmp/router-fixture/usr/lib/owrt-remote/panel-update.sh '

            def start(operation='check'):
                return json.loads(remote(env + helper + operation))

            def wait():
                for _ in range(70):
                    value = start('status')
                    if value['status'] not in ('checking', 'downloading', 'installing'):
                        log.write(json.dumps(value, ensure_ascii=False) + '\n'); log.flush()
                        return value
                    time.sleep(.3)
                raise AssertionError('VM update timed out')

            hashes = ' '.join('/tmp/router-fixture/' + name for name in case.protected)
            remote('sha256sum ' + hashes + ' >/tmp/protected.sha256')
            mode['delay'] = 3
            first = start(); second = start()
            assert first['id'] == second['id'], (first, second)
            assert wait()['status'] == 'available'
            mode['delay'] = 0
            print('Concurrent checks serialized; installing verified build…', flush=True)
            start('install ' + fixture.COMMIT)
            result = wait()
            assert result['status'] == 'succeeded', result
            remote('sha256sum -c /tmp/protected.sha256')
            start(); assert wait()['status'] == 'current'
            print('Testing HTTP error and bad checksums…', flush=True)
            mode['api_fail'] = True
            start(); assert wait()['status'] == 'failed'
            mode['api_fail'] = False
            with (case.source / 'usr/lib/owrt-remote/wan.sh').open('a', encoding='utf-8', newline='\n') as stream:
                stream.write('\n# same-version fixture update\n')
            case.make_manifest()
            mode['corrupt'] = True
            start('install ' + fixture.COMMIT)
            assert 'Контрольная сумма' in wait()['message']
            mode['corrupt'] = False
            (case.source / 'etc/init.d/owrt-remote').write_text('#!/bin/sh\nexit 1\n', newline='\n')
            case.make_manifest()
            print('Testing failed service restart and rollback…', flush=True)
            start('install ' + fixture.COMMIT)
            result = wait()
            assert result['status'] == 'failed' and 'Предыдущая сборка восстановлена' in result['message'], result
            remote('sha256sum -c /tmp/protected.sha256')
            start('rollback'); assert wait()['status'] == 'succeeded'
            print('Testing interrupted transaction recovery and snapshot integrity…', flush=True)
            remote('set -e; touch /tmp/router-fixture/etc/owrt-remote/panel-update-backup/transaction; printf broken >/tmp/router-fixture/www/cgi-bin/owrt-remote')
            start(); wait()
            remote('set -e; test ! -e /tmp/router-fixture/etc/owrt-remote/panel-update-backup/transaction; grep -q routerUpdateDialog /tmp/router-fixture/www/cgi-bin/owrt-remote; sha256sum -c /tmp/protected.sha256')
            print('Testing real CGI authentication/CSRF dispatch…', flush=True)
            cgi = env + "KEY_FILE=/tmp/router-fixture/etc/owrt-remote/web.key PANEL_UPDATER=/tmp/router-fixture/usr/lib/owrt-remote/panel-update.sh REQUEST_METHOD=GET QUERY_STRING='key=preserve%20etc%2Fowrt-remote%2Fweb.key&action=panel_update&operation=status' sh /tmp/router-fixture/www/cgi-bin/owrt-remote"
            assert '"ok":true' in remote(cgi)
            assert '403 Forbidden' in remote(cgi.replace('operation=status', 'operation=check'))
            remote('printf corrupt >>/tmp/router-fixture/etc/owrt-remote/panel-update-backup/www/cgi-bin/owrt-remote')
            start('rollback'); assert wait()['status'] == 'failed'
            remote('sha256sum -c /tmp/protected.sha256; test ! -e /tmp/router-fixture/tmp/owrt-remote-panel-update/lock')
            (output / 'result.txt').write_text('OPENWRT_ROUTER_PANEL_UPDATE_VM_OK\n', encoding='utf-8')
            print('OPENWRT_ROUTER_PANEL_UPDATE_VM_OK', flush=True)
        finally:
            if vm is not None:
                vm.terminate()
                try: vm.wait(timeout=10)
                except subprocess.TimeoutExpired: vm.kill(); vm.wait(timeout=10)
                qlog.close()
            if server: server.shutdown(); server.server_close()
            if 'plain' in locals(): plain.shutdown(); plain.server_close()
            if 'log' in locals(): log.close()
            case.doCleanups()


if __name__ == '__main__':
    main()
