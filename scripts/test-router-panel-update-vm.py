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
import re
import shlex
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
            # Exercise the actual web server contract, not a shell environment
            # manually supplying a custom HTTP_X_* variable that uhttpd omits.
            http_files = {
                # uhttpd also clears inherited process environment. Inject only
                # isolated fixture paths into this test copy of the real CGI.
                'www/cgi-bin/owrt-remote': (ROOT / 'files/www/cgi-bin/owrt-remote').read_bytes().replace(
                    b'#!/bin/sh\n', b'#!/bin/sh\nKEY_FILE=/tmp/uhttpd-fixture/web.key\nPANEL_UPDATER=/tmp/uhttpd-fixture/updater.sh\nAGENT=/tmp/uhttpd-fixture/agent\n', 1),
                'www/cgi-bin/headers': b'#!/bin/sh\nprintf "Content-Type: text/plain\\r\\n\\r\\ncustom=%s\\n" "$HTTP_X_OWRT_PANEL_UPDATE"\n',
                'updater.sh': b'#!/bin/sh\nprintf "%s\\n" "$*" >>/tmp/uhttpd-fixture/calls\nstate="$1"; [ "$state" != status ] || state=idle\nprintf \'{"ok":true,"status":"%s"}\\n\' "$state"\n',
                'agent': case.old_agent.encode(),
                'web.key': b'secret\n',
            }
            http_root = work / 'uhttpd-fixture'
            for name, data in http_files.items():
                path = http_root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o755 if name.startswith('www/cgi-bin/') or name == 'agent' else 0o600)
            with tarfile.open(archive, 'w') as tar:
                tar.add(case.root, arcname='router-fixture')
                tar.add(http_root, arcname='uhttpd-fixture')
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
            remote('if ! command -v uhttpd >/dev/null; then if command -v apk >/dev/null; then apk update && apk add uhttpd; else opkg update && opkg install uhttpd; fi; fi', timeout=240)
            origin = 'http://192.168.1.2:' + str(plain.server_port)
            remote(f"set -e; wget -qO /tmp/fixture.tar '{origin}/fixture.tar'; tar -xf /tmp/fixture.tar -C /tmp; wget -qO /tmp/update-cert.pem '{origin}/cert.pem'; mkdir /tmp/update-bin")
            wrapper = '#!/bin/sh\nexec /usr/bin/curl --cacert /tmp/update-cert.pem --connect-to api.github.com:443:192.168.1.2:' + str(server.server_port) + ' --connect-to raw.githubusercontent.com:443:192.168.1.2:' + str(server.server_port) + ' "$@"\n'
            remote('printf %s ' + shlex.quote(wrapper) + ' >/tmp/update-bin/curl; chmod 755 /tmp/update-bin/curl')
            # Fail secondary transports locally: an injected API failure must
            # not fall back to the real public GitHub outside this fixture.
            blocked = '#!/bin/sh\necho "fixture secondary transport disabled" >&2\nexit 1\n'
            for tool in ('uclient-fetch', 'wget'):
                remote('printf %s ' + shlex.quote(blocked) + ' >/tmp/update-bin/' + tool + '; chmod 755 /tmp/update-bin/' + tool)
            print('Testing real uhttpd POST confirmation…', flush=True)
            # Windows does not preserve executable mode when creating this tar.
            remote('chmod 755 /tmp/uhttpd-fixture/www/cgi-bin/* /tmp/uhttpd-fixture/agent')
            remote('uhttpd -f -p 127.0.0.1:18080 -h /tmp/uhttpd-fixture/www -x /cgi-bin </dev/null >/tmp/uhttpd-fixture/server.log 2>&1 &')

            def http_request(query, body=None, header=False):
                command = 'curl -sS --max-time 15 -w ' + shlex.quote('\n%{http_code}')
                if body is not None:
                    command += ' --data ' + shlex.quote(body)
                if header:
                    command += ' -H ' + shlex.quote('X-OWRT-Panel-Update: 1')
                command += ' ' + shlex.quote('http://127.0.0.1:18080/cgi-bin/owrt-remote?' + query)
                value, status = remote(command).rsplit('\n', 1)
                return int(status), value

            assert remote("curl -fsS -H 'X-OWRT-Panel-Update: 1' http://127.0.0.1:18080/cgi-bin/headers") == 'custom='
            query = 'key=secret&action=panel_update&operation='
            assert http_request(query + 'check', body='', header=True)[0] == 403
            code, page = http_request('key=secret')
            assert code == 200
            proof = re.search(r'data-router-update-token="([0-9a-f]{64})"', page)[1]
            assert proof == hashlib.sha256(b'owrt-remote:panel-update:v1:secret').hexdigest()
            assert http_request(query + 'status')[0] == 200
            for operation in ('check', 'install'):
                code, body = http_request(query + operation, 'panel_update_token=' + proof + '&revision=' + fixture.COMMIT)
                assert code == 200 and json.loads(body)['ok'], (code, body)
            calls = remote('cat /tmp/uhttpd-fixture/calls')
            assert 'install ' + fixture.COMMIT in calls
            for operation in ('check', 'install'):
                for bad_query, bad_body in ((query + operation, ''),
                                            (query + operation, 'panel_update_token=wrong'),
                                            (query + operation + '&panel_update_token=' + proof, '')):
                    assert http_request(bad_query, bad_body)[0] == 403
                assert http_request(query + operation + '&panel_update_token=' + proof)[0] == 403
            code, page = http_request('key=wrong', 'panel_update_token=' + proof)
            assert 'data-router-update-token=' not in page
            assert remote('cat /tmp/uhttpd-fixture/calls') == calls
            (output / 'http-post.txt').write_text('UHTTPD_ROUTER_UPDATE_POST_OK\n', encoding='utf-8')
            print('UHTTPD_ROUTER_UPDATE_POST_OK', flush=True)
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
