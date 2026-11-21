"""Per-router discovery lifecycle, authenticated proxy and fixed reverse targets."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import test_host_policy as base
hub = base.hub


def detected(**values):
    return dict(checked=True, installed=True, version='2.0.0~beta.8-1',
                mode='standalone', port=1680, service='running', available=True, **values)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'hub.db'
        self.context = hub.connect(self.db)
        self.conn = self.context.__enter__()
        hub.init_db(self.conn)
        self.addCleanup(self.context.__exit__, None, None, None)
        for rid, port in [('a', 18080), ('b', 18081)]:
            hub.upsert_router(self.conn, dict(id=rid, entry_port=port))

    def beat(self, rid='a', tachyon=None):
        return hub.heartbeat(self.conn, dict(id=rid, xray='running', tachyon=tachyon))

    def test_independent_install_stop_remove_reinstall_and_network_recovery(self):
        a = self.beat(tachyon=detected())
        self.assertTrue(a['tachyon']['online'])
        self.assertNotIn('tachyon', self.beat('b', {'checked': True, 'installed': False}))
        port = a['tachyon_entry_port']
        self.assertTrue(a['_xray_reload_required'])
        value = detected(); value.update(service='stopped', available=False)
        a = self.beat(tachyon=value)
        self.assertFalse(a['tachyon']['online'])
        self.assertFalse(a['_xray_reload_required'], 'service status must not restart every tunnel')
        self.assertEqual(a['tachyon']['url'], '/tachyon/a/')
        a = self.beat(tachyon={'checked': False})
        self.assertIn('tachyon', a, 'inventory error is not uninstall')
        self.assertFalse(a['tachyon']['online'])
        a = self.beat(tachyon={'checked': True, 'installed': False})
        self.assertNotIn('tachyon', a)
        self.assertTrue(a['_xray_reload_required'])
        a = self.beat(tachyon=detected())
        self.assertEqual(a['tachyon_entry_port'], port)
        self.conn.execute("update routers set last_seen=1 where id='a'")
        self.assertFalse(hub.row_to_router(hub.get_router(self.conn, 'a'))['tachyon']['online'])
        self.assertTrue(self.beat(tachyon=detected())['tachyon']['online'])

    def test_bad_endpoints_never_create_arbitrary_target(self):
        for patch in ({'port': 22}, {'port': '1680'}, {'port': 65536}, {'mode': 'http://evil'},
                      {'port': True}, {'checked': 'true'}, {'installed': 'true'}):
            value = detected(); value.update(patch)
            self.assertIsNone(hub.normalize_tachyon(value), patch)
        value = detected(); value.update(host='169.254.169.254', token='SECRET')
        clean = hub.normalize_tachyon(value)
        self.assertNotIn('host', clean); self.assertNotIn('token', clean)

    def test_both_generators_share_fixed_loopback_target_and_existing_reverse(self):
        a = self.beat(tachyon=detected())
        row = hub.get_router(self.conn, 'a')
        server = hub.make_server_xray_config([row])
        inbound = next(i for i in server['inbounds'] if i['tag'] == 'tachyon-a')
        self.assertEqual(inbound['listen'], '127.0.0.1')
        self.assertEqual(inbound['settings']['rewriteAddress'], '127.0.0.1')
        self.assertEqual(inbound['settings']['rewritePort'], 1680)
        self.assertEqual(inbound['settings']['address'], inbound['settings']['rewriteAddress'])
        self.assertEqual(inbound['settings']['port'], inbound['settings']['rewritePort'])
        self.assertEqual(inbound['settings']['network'], 'tcp')
        client = hub.make_router_xray_config(row)
        rule = client['routing']['rules'][0]
        self.assertEqual(rule['outboundTag'], 'router-tachyon')
        self.assertEqual(rule['ip'], ['127.0.0.1'])
        self.assertEqual(client['outbounds'][0]['settings']['redirect'], '127.0.0.1:1680')
        self.assertEqual(len(server['inbounds'][0]['settings']['clients']), 2)
        self.assertEqual(hub.get_router_by_any_port(self.conn, a['tachyon_entry_port'])['id'], 'a')
        with self.assertRaisesRegex(ValueError, 'Tachyon'):
            hub.upsert_router(self.conn, {'id': 'a', 'entry_port': a['tachyon_entry_port']})

    def test_legacy_luci_uses_existing_admin_tunnel(self):
        value = detected(); value.update(mode='luci', port=0, version='1.3.13')
        self.beat(tachyon=value)
        self.assertFalse(any(i['tag'] == 'tachyon-a' for i in hub.make_server_xray_config([hub.get_router(self.conn, 'a')])['inbounds']))

    def test_existing_report_with_no_entry_port_still_requests_server_reload(self):
        self.conn.execute("update routers set status_json=? where id='a'", (json.dumps({'tachyon': detected()}),))
        self.conn.commit()
        router = self.beat(tachyon=detected())
        self.assertGreater(router['tachyon_entry_port'], 0)
        self.assertTrue(router['_xray_reload_required'])

    def test_rewrite_keeps_api_and_assets_inside_selected_router(self):
        body = b'<html><head></head><body><script>fetch("/api/login");fetch("/ubus");</script><img src="/assets/icon.png"></body></html>'
        rendered = hub.rewrite_html(body, '/tachyon/a', 'text/html').decode()
        for path in ('/api/login', '/ubus', '/assets/icon.png'):
            self.assertIn('/tachyon/a' + path, rendered)
        self.assertNotIn('/tachyon/a/api/login', hub.rewrite_html(body, '/access/a', 'text/html').decode())


@unittest.skipUnless(base.SH, 'POSIX shell required')
class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.harness = base.RouterShellTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.harness.config(vps_host='hub.example', vless_uuid='11111111-1111-4111-8111-111111111111')
        self.root = self.harness.directory
        self.harness.env.update(OWRT_REMOTE_TACHYON_HELPER=str(base.ROOT / 'files/usr/lib/owrt-remote/tachyon.sh').replace('\\', '/'),
                                OWRT_REMOTE_TACHYON_RUNTIME=self.root.as_posix() + '/tachyon')
        if os.environ.get('TACHYON_TEST_BUSYBOX'):
            def busybox_shell(command, **env):
                mocks = re.sub(r'(?ms)^owrt-remote\(\) \{.*?^\}\n', '', base.MOCKS)
                return subprocess.run([os.environ['TACHYON_TEST_BUSYBOX'], 'ash'],
                    input=self.harness.agent + '\n' + mocks + '\n' + command,
                    env={**self.harness.env, **env}, cwd=base.ROOT, capture_output=True,
                    encoding='utf-8', timeout=60)
            self.harness.run_shell = busybox_shell
        self.mocks = r'''
opkg() {
    [ ! -e "$TEST_ROOT/inventory-error" ] || return 1
    [ ! -e "$TEST_ROOT/absent" ] || return 0
    printf 'Package: tachyon\nVersion: %s\nStatus: install user installed\n' "$TEST_VERSION"
}
curl() {
    [ "$TEST_HTTP" != fail ] || return 7
    for last do :; done
    case "$last" in
        */api/agent/v1/health) [ "${TEST_API:-1}" = 1 ] || return 22; printf '{"core_running":false}' ;;
        */api/agent/v1/openapi.json) printf '{"info":{"version":"2.0.0-beta.14"}}' ;;
        *) printf '%s' "$TEST_HTTP" ;;
    esac
}
ubus() { printf '{"running":%s}' "$TEST_SERVICE"; }
jsonfilter() {
    "$TEST_PYTHON" -c 'import sys,json
a=sys.argv[1:]; data=json.load(open(a[1])) if a and a[0]=="-i" else json.load(sys.stdin)
path=a[-1]
if "instances" in path: value=data.get("running")
elif "router-tachyon" in path: value=next((o["settings"]["redirect"] for o in data.get("outbounds",[]) if o["tag"]=="router-tachyon"),"")
else:
 value=data
 for key in path.removeprefix("@.").split("."): value=value.get(key,"") if isinstance(value,dict) else ""
print(str(value).lower() if isinstance(value,bool) else value)' "$@"
}
'''

    def run_shell(self, command, **env):
        return self.harness.run_shell(self.mocks + command, TEST_VERSION='2.0.0~beta.8-1',
                                      TEST_HTTP='200', TEST_SERVICE='true', **env)

    def test_opkg_present_http_offline_inventory_failure_and_uninstall(self):
        result = self.run_shell('tachyon_json force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['port'], 1680)
        self.assertEqual(json.loads(result.stdout)['api'], '/api/agent/v1')
        self.assertEqual(json.loads(result.stdout)['version'], '2.0.0-beta.14')
        self.assertEqual(json.loads(result.stdout)['package_version'], '2.0.0~beta.8-1')
        self.assertTrue(json.loads(result.stdout)['available'])
        result = self.harness.run_shell(self.mocks + 'tachyon_json force', TEST_VERSION='2.0.0', TEST_HTTP='fail', TEST_SERVICE='false')
        self.assertTrue(json.loads(result.stdout)['installed'])
        self.assertFalse(json.loads(result.stdout)['available'])
        self.root.joinpath('inventory-error').touch()
        self.assertFalse(json.loads(self.run_shell('tachyon_json force').stdout)['checked'])
        self.root.joinpath('inventory-error').unlink()
        self.root.joinpath('absent').touch()
        self.assertEqual(json.loads(self.run_shell('tachyon_json force').stdout), dict(checked=True, installed=False))
        self.assertFalse(list(self.root.glob('tachyon/probe.*')))

    def test_apk_discovery_and_legacy_luci(self):
        code = r'''
apk() {
    case "$1" in
        info) printf 'base-files\ntachyon\n' ;;
        list) printf 'tachyon 2.0.0-r1\n' ;;
        *) return 1 ;;
    esac
}
tachyon_json force
'''
        result = self.run_shell(code)
        self.assertEqual(json.loads(result.stdout)['package_version'], '2.0.0-r1')
        with (self.root / 'uci').open('a') as config:
            config.write('tachyon.settings.listen_port=1602\n')
        result = self.harness.run_shell(self.mocks + 'tachyon_json force', TEST_VERSION='1.3.13', TEST_HTTP='403', TEST_SERVICE='true', TEST_API='0')
        self.assertEqual(json.loads(result.stdout)['mode'], 'luci')
        self.assertEqual(json.loads(result.stdout)['port'], 0)

    def test_apk_305_description_is_not_a_version_and_manifest_selects_standalone(self):
        result = self.run_shell(r'''
apk() {
    case "$1:$2" in
        info:) printf 'base-files\ntachyon\n' ;;
        info:-v) printf 'tachyon: Pure-Rust routing orchestrator and web panel for OpenWrt (aarch64)\ntachyon: https://github.com/Dushnilin/tachyon\ntachyon: 7136 KiB\n' ;;
        list:--installed) printf 'tachyon 2.0.0~beta.15-r1\n' ;;
        *) return 1 ;;
    esac
}
tachyon_json force
''')
        status = json.loads(result.stdout)
        self.assertEqual(status['mode'], 'standalone')
        self.assertEqual(status['port'], 1680)
        self.assertEqual(status['package_version'], '2.0.0~beta.15-r1')
        self.assertTrue(status['available'])

    def test_actual_api_recovers_missing_or_stale_package_version(self):
        for version in ('', '1.3.13'):
            result = self.harness.run_shell(self.mocks + 'tachyon_json force',
                                            TEST_VERSION=version, TEST_HTTP='200', TEST_SERVICE='true')
            status = json.loads(result.stdout)
            self.assertEqual(status['mode'], 'standalone')
            self.assertEqual(status['port'], 1680)
            self.assertEqual(status['version'], '2.0.0-beta.14')

    def test_luci_login_response_is_not_proof_of_an_unknown_tachyon_ui(self):
        result = self.harness.run_shell(self.mocks + 'tachyon_json force',
                                        TEST_VERSION='', TEST_API='0', TEST_HTTP='403', TEST_SERVICE='true')
        self.assertFalse(json.loads(result.stdout)['available'])

    def test_custom_web_port_and_no_reconfigure_when_manually_stopped(self):
        with (self.root / 'uci').open('a') as config:
            config.write('tachyon.settings.listen_port=1800\n')
        result = self.run_shell('tachyon_json force')
        self.assertEqual(json.loads(result.stdout)['port'], 1800)
        self.harness.config(enabled='1')
        result = self.run_shell(r'''
xray_control_stopped() { return 0; }
xray_running() { return 0; }
ubus() { echo unexpected >>"$TEST_ROOT/changed"; }
tachyon_reconcile_xray
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'changed').exists())

    def test_shell_generator_matches_tachyon_route(self):
        result = self.run_shell('render_client_config --stdout')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config['outbounds'][0]['settings']['redirect'], '127.0.0.1:1680')
        self.assertEqual(config['routing']['rules'][0]['outboundTag'], 'router-tachyon')


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = hub.App(Path(self.temp.name) / 'hub.db', 'session-test', 'agent-test', '')
        hub.write_json_private(hub.AUTH_FILE, {'username': 'owner', 'password_hash': 'unused'})
        class Backend(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/layout':
                    data = b'<html><head><style>.tachyon-bg-layer{position:fixed;inset:0}</style></head><body><div class="tachyon-bg-layer"></div><main>Tachyon</main></body></html>'
                    self.send_response(200); self.send_header('Content-Type', 'text/html')
                    self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
                    return
                data = json.dumps({'path': self.path, 'cookie': self.headers.get('Cookie', ''),
                                   'method': self.command, 'body': self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Set-Cookie', 'tachyon_session=test-private; Path=/; HttpOnly')
                self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
            do_POST = do_GET
            do_PUT = do_GET
            do_DELETE = do_GET
            def log_message(self, *args): pass
        self.backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        self.server = hub.HubHTTPServer(('127.0.0.1', 0), hub.Handler)
        self.server.app = self.app; self.server.is_tls = False
        for server in (self.backend, self.server):
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            self.addCleanup(self.close, server, thread)
        with self.app.conn() as conn:
            hub.upsert_router(conn, {'id': 'main', 'entry_port': 18880})
            hub.heartbeat(conn, {'id': 'main', 'tachyon': detected()})
            conn.execute("update routers set tachyon_entry_port=? where id='main'", (self.backend.server_port,))
            hub.upsert_router(conn, {'id': 'absent'})

    @staticmethod
    def close(server, thread):
        server.shutdown(); server.server_close(); thread.join(timeout=3)

    def request(self, path='/tachyon/main/', method='GET', origin=None, cookie=True):
        headers = {}
        if cookie: headers['Cookie'] = 'owrt_remote_session=session-test; tachyon_session=private-router-session'
        if origin: headers['Origin'] = origin
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body='{}' if method != 'GET' else None, headers=headers)
            with connection.getresponse() as result: return result.status, result.read(), result.getheaders()
        finally: connection.close()

    def test_proxy_authentication_selected_router_cookie_scope_and_mutations(self):
        self.assertEqual(self.request(cookie=False)[0], 401)
        status, raw, headers = self.request('/tachyon/main/api/status?view=x')
        self.assertEqual(status, 200, raw)
        result = json.loads(raw)
        self.assertEqual(result['path'], '/api/status?view=x')
        self.assertNotIn('session-test', result['cookie'])
        self.assertIn('private-router-session', result['cookie'])
        cookie = next(v for k, v in headers if k.lower() == 'set-cookie')
        self.assertIn('Path=/tachyon/main/', cookie)
        self.assertEqual(self.request('/tachyon/absent/')[0], 404)
        self.assertEqual(self.request('/tachyon/missing/')[0], 404)
        with mock.patch.object(hub.Handler, 'router_access_allowed', return_value=False):
            self.assertEqual(self.request()[0], 403)
        self.assertEqual(self.request(method='POST', origin='https://foreign.example')[0], 403)
        origin = 'http://127.0.0.1:' + str(self.server.server_port)
        for method in ('POST', 'PUT', 'DELETE'):
            status, raw, _ = self.request('/tachyon/main/api/settings', method, origin)
            self.assertEqual(status, 200, raw)
            self.assertEqual(json.loads(raw)['method'], method)
            self.assertEqual(json.loads(raw)['body'], '{}')

    def test_tachyon_html_keeps_own_layout_without_shared_hub_effects(self):
        status, raw, _ = self.request('/tachyon/main/layout')
        self.assertEqual(status, 200)
        self.assertNotIn(b'owrt-hub-design-head', raw)
        self.assertNotIn(b'body>*:not(.seasonalFx)', raw)
        self.assertIn(b'.tachyon-bg-layer{position:fixed;inset:0}', raw)
        self.assertIn(b'const prefix = "/tachyon/main"', raw, 'URL forwarding still applies')


if __name__ == '__main__':
    unittest.main()
