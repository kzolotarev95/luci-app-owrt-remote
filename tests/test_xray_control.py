"""Heartbeat Xray commands: real HTTP/SQLite flow, no SSH or VPS execution."""
import concurrent.futures
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import test_router_log_archive as base

hub = base.hub
ROOT = base.ROOT


class ControlTests(unittest.TestCase):
    setUp = base.ArchiveTests.setUp
    close = base.ArchiveTests.close
    request = base.ArchiveTests.request
    def beat(self, router='main', result=None, capability=1):
        payload = {'id': router, 'name': 'Роутер тест', 'xray': 'stopped', 'xray_control': capability}
        if result:
            payload['xray_control_result'] = result
        raw, _ = self.request('/api/heartbeat', payload, cookie=False, agent=True)
        return json.loads(raw)

    def command(self, action='start', router='main', request_id='a' * 32):
        with self.app.conn() as conn:
            hub.xray_control_enqueue(conn, router, action, request_id)

    def snapshot_control(self, router='main'):
        raw, _ = self.request('/api/router/' + router + '/xray-control')
        return json.loads(raw)

    def test_stopped_xray_receives_command_by_heartbeat_without_ssh(self):
        self.beat()
        self.command()
        with mock.patch.object(hub.subprocess, 'run', side_effect=AssertionError('must not execute on VPS')):
            reply = self.beat()
        self.assertEqual(reply['xray_command'], {'id': 'a' * 32, 'action': 'start'})
        self.assertIsNone(self.beat('other')['xray_command'])
        # Lost response redelivers the same ID until the agent confirms receipt.
        self.assertEqual(self.beat()['xray_command'], reply['xray_command'])
        result = {'id': 'a' * 32, 'action': 'start', 'status': 'running', 'rc': 0, 'xray': 'stopped', 'message': 'Accepted'}
        self.assertIsNone(self.beat(result=result)['xray_command'])
        result.update(status='succeeded', xray='running', message='Process verified')
        self.beat(result=result)
        self.beat(result=result)
        snapshot = self.snapshot_control()
        self.assertEqual(snapshot['history'][0]['status'], 'succeeded')
        with self.app.conn() as conn:
            log = hub.router_log_snapshot(conn, 'main')[1].decode()
        self.assertEqual(log.count('Process verified'), 1)

    def test_invalid_results_cannot_fake_success_or_affect_other_router(self):
        self.beat(); self.command(); self.beat()
        result = {'id': 'a' * 32, 'action': 'start', 'status': 'succeeded', 'rc': 0, 'xray': 'stopped', 'message': 'not started'}
        self.beat('other', result)
        self.assertEqual(self.snapshot_control()['history'][0]['status'], 'delivered')
        self.beat(result=result)
        self.assertEqual(self.snapshot_control()['history'][0]['status'], 'failed')

    def test_api_access_csrf_and_bounded_input(self):
        import urllib.request
        import urllib.error
        url = f'http://127.0.0.1:{self.server.server_port}/api/router/main/xray-control'
        def post(headers, body=None):
            return urllib.request.urlopen(urllib.request.Request(url, data=body or b'{}', headers=headers), timeout=5)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/router/main/xray-control', cookie=False)
        self.assertEqual(error.exception.code, 401)
        headers = {'Cookie': 'owrt_remote_session=session-test', 'Content-Type': 'application/json'}
        with self.assertRaises(urllib.error.HTTPError) as error:
            post(headers)
        self.assertEqual(error.exception.code, 403)
        headers['X-Xray-Control-CSRF'] = self.app.xray_control_csrf
        self.beat()
        with post(headers, json.dumps({'action': 'stop', 'request_id': 'b' * 32}).encode()) as response:
            self.assertEqual(json.load(response)['history'][0]['action'], 'stop')
        headers['Origin'] = 'https://external.invalid'
        with self.assertRaises(urllib.error.HTTPError) as error:
            post(headers)
        self.assertEqual(error.exception.code, 403)
        page, _ = self.request('/router/main/xray-control')
        self.assertIn('Remote управление Xray'.encode(), page)

    def test_concurrent_tabs_idempotency_and_legacy_agent(self):
        self.beat(capability=0)
        with self.assertRaisesRegex(ValueError, 'обнови агент'):
            self.command()
        self.beat()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.command(), range(4)))
        self.assertEqual(len(self.snapshot_control()['history']), 1)
        with self.assertRaisesRegex(ValueError, 'Предыдущая команда'):
            self.command('stop', request_id='b' * 32)

    def test_clear_history_hides_completed_output_but_keeps_active_command(self):
        self.beat(); self.command(); self.beat()
        self.beat(result={'id': 'a' * 32, 'action': 'start', 'status': 'succeeded', 'rc': 0,
                          'xray': 'running', 'message': 'старый результат'})
        self.command('stop', request_id='b' * 32)
        with self.app.conn() as conn:
            hub.xray_control_clear_history(conn, 'main')
            snapshot = hub.xray_control_snapshot(conn, 'main')
        self.assertEqual([item['action'] for item in snapshot['history']], ['stop'])
        self.assertEqual(snapshot['history'][0]['status'], 'queued')
        self.assertEqual(snapshot['heartbeat_interval'], 30)
        self.assertEqual(snapshot['command_timeout_seconds'], 180)
        with self.app.conn() as conn:
            hidden = conn.execute("select hidden,result from router_xray_commands where id=?", ('a' * 32,)).fetchone()
        self.assertEqual(tuple(hidden), (1, ''))
        self.assertEqual(self.beat()['xray_command']['id'], 'b' * 32)
        self.beat(result={'id': 'b' * 32, 'action': 'stop', 'status': 'succeeded', 'rc': 0,
                          'xray': 'stopped', 'message': 'остановлен'})
        # A cleared command ID stays reserved: retry/late results cannot replay it.
        self.command(request_id='a' * 32)
        self.assertIsNone(self.beat()['xray_command'])
        self.assertEqual(len(self.snapshot_control()['history']), 1)

    def test_clear_api_auth_isolates_router_and_survives_restart_and_late_results(self):
        import urllib.request
        import urllib.error
        self.beat(); self.command(); self.beat()
        self.beat('other'); self.command(router='other', request_id='c' * 32)
        with mock.patch.object(hub, 'now_ts', return_value=hub.now_ts()+181):
            self.snapshot_control()  # A delivered command becomes unconfirmed.
        url = f'http://127.0.0.1:{self.server.server_port}/api/router/main/xray-control'
        body = json.dumps({'action': 'clear_history'}).encode()
        headers = {'Content-Type': 'application/json', 'Cookie': 'owrt_remote_session=session-test'}
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=5)
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(len(self.snapshot_control()['history']), 1)
        with self.app.conn() as conn:
            original_logs = hub.router_log_snapshot(conn, 'main')[1]
        headers['X-Xray-Control-CSRF'] = self.app.xray_control_csrf
        for _ in range(2):
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=5) as response:
                self.assertEqual(json.load(response)['history'], [])
        self.assertEqual(len(self.snapshot_control('other')['history']), 1)
        self.server.app = self.app = hub.App(self.app.db_path, 'session-test', 'agent-test', '')
        self.beat(result={'id': 'a' * 32, 'action': 'start', 'status': 'succeeded', 'rc': 0,
                          'xray': 'running', 'message': 'поздний результат'})
        self.assertEqual(self.snapshot_control()['history'], [])
        with self.app.conn() as conn:
            self.assertEqual(hub.router_log_snapshot(conn, 'main')[1], original_logs)

    def test_restart_persistence_offline_expiry_and_late_result(self):
        self.beat(); self.command()
        self.server.app = self.app = hub.App(self.app.db_path, 'session-test', 'agent-test', '')
        reply = self.beat()
        self.assertEqual(reply['xray_command']['id'], 'a' * 32)
        with mock.patch.object(hub, 'now_ts', return_value=hub.now_ts() + 181):
            self.assertEqual(self.snapshot_control()['history'][0]['status'], 'unconfirmed')
        self.beat(result={'id': 'a' * 32, 'action': 'start', 'status': 'succeeded', 'rc': 0, 'xray': 'running', 'message': 'late result'})
        self.assertEqual(self.snapshot_control()['history'][0]['status'], 'succeeded')
        self.command('stop', request_id='b' * 32)
        with mock.patch.object(hub, 'now_ts', return_value=hub.now_ts() + 181):
            self.assertIsNone(self.beat()['xray_command'])
            self.assertEqual(self.snapshot_control()['history'][0]['status'], 'expired')

    def test_ui_script_syntax(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node required')
        page = hub.router_xray_control_html({'id': 'main', 'name': '</script><script>alert(1)</script>'}, 'csrf')
        script = re.search(r'<script>(.*?)</script>', page, re.S)[1]
        result = subprocess.run([node, '--check'], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('<h2 id="routerName"></script>', page)


if __name__ == '__main__':
    unittest.main()
