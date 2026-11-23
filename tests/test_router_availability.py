"""Persistent availability observations, bounded storage and scoped HTTP output."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

STATE = tempfile.TemporaryDirectory(prefix='owrt-availability-')
SOURCE = Path(__file__).resolve().parents[1] / 'vps/owrt-remote-hub.py'
spec = importlib.util.spec_from_file_location('availability_hub', SOURCE)
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {'OWRT_REMOTE_STATE_DIR': STATE.name}):
    spec.loader.exec_module(hub)


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=STATE.name)
        self.addCleanup(self.tmp.cleanup)
        self.app = hub.App(Path(self.tmp.name) / 'hub.db', 'session', 'agent', '')
        self.history = self.app.router_availability
        self.at = 1795300020  # aligned to a minute
        self.routers = [{'id': 'a', 'online': True}, {'id': 'b', 'online': False}]

    def test_records_without_browser_and_uses_real_heartbeat_timeout(self):
        with self.app.conn() as conn:
            for router in self.routers:
                hub.upsert_router(conn, {'id': router['id'], 'name': router['id']})
            conn.execute("update routers set last_seen=?,status_json='{}' where id='a'", (self.at,))
        with mock.patch.object(hub, 'now_ts', return_value=self.at):
            self.app.prime_router_state_snapshot()
        with mock.patch.object(hub, 'now_ts', return_value=self.at + hub.ONLINE_AFTER_SECONDS + 60), \
                mock.patch.object(hub, 'notify_router_offline'):
            self.app.check_router_state_changes()
        with self.app.conn() as conn:
            result = self.history.snapshot(conn, self.routers, self.at + 300)
        self.assertEqual(result['points'][0][1:], [1, 2])
        self.assertEqual(result['points'][-1][1:], [0, 2])
        self.assertEqual(result['from'], self.at + 300 - 86400)

    def test_once_per_minute_restart_and_missing_intervals(self):
        with self.app.conn() as conn:
            self.history.record(conn, self.routers, self.at)
            self.history.record(conn, self.routers, self.at + 5)
            self.assertEqual(conn.execute('select count(*) from router_availability').fetchone()[0], 2)
        restarted = hub.RouterAvailabilityHistory()
        with self.app.conn() as conn:
            self.assertEqual(restarted.snapshot(conn, self.routers, self.at + 30)['points'], [[self.at, 1, 2]])
            restarted.record(conn, self.routers, self.at + 30)
            restarted.record(conn, self.routers, self.at + 600)
            points = restarted.snapshot(conn, self.routers, self.at + 600)['points']
        self.assertEqual([p[0] for p in points], [self.at + 30, self.at + 600], 'no invented observations during downtime')

    def test_storage_retention_and_shared_bounded_read_cache(self):
        with self.app.conn() as conn:
            for minute in range(3 * 1440):
                self.history.record(conn, self.routers, self.at + minute * 60)
            self.assertLessEqual(conn.execute('select count(*) from router_availability').fetchone()[0], 2 * 1561)
            now = self.at + (3 * 1440 - 1) * 60
            first = self.history.snapshot(conn, self.routers, now)
            queries = []
            conn.set_trace_callback(queries.append)
            second = self.history.snapshot(conn, self.routers, now + 1)
            self.assertIs(first['points'], second['points'], 'concurrent viewers reuse the aggregate')
            self.assertFalse(queries)
            self.assertLessEqual(len(first['points']), 1442)
            for i in range(30):
                self.history.snapshot(conn, [{'id': str(i)}], now)
            self.assertLessEqual(len(self.history.cache), 16)

    def test_permission_scope_and_empty_fleet(self):
        with self.app.conn() as conn:
            self.history.record(conn, self.routers, self.at)
            self.assertEqual(self.history.snapshot(conn, [{'id': 'b'}], self.at)['points'], [[self.at, 0, 1]])
            self.assertEqual(self.history.snapshot(conn, [], self.at)['points'], [])
            self.assertEqual(self.history.snapshot(conn, [{'id': 'unassigned'}], self.at)['points'], [])

    def test_failed_commit_does_not_suppress_retry(self):
        with self.app.conn() as conn:
            failing = mock.Mock(wraps=conn)
            failing.commit.side_effect = sqlite3.OperationalError('disk full')
            with self.assertRaises(sqlite3.OperationalError):
                self.history.record(failing, self.routers, self.at)
            self.assertIsNone(self.history.last_bucket)
            self.history.record(conn, self.routers, self.at)
            self.assertEqual(self.history.snapshot(conn, self.routers, self.at)['points'], [[self.at, 1, 2]])

    def test_http_history_uses_visible_routers_and_requires_login(self):
        class QuietHandler(hub.Handler):
            def log_message(self, *args):
                pass
        with self.app.conn() as conn:
            for router in self.routers:
                hub.upsert_router(conn, {'id': router['id'], 'name': router['id']})
            self.history.record(conn, self.routers)
        server = hub.HubHTTPServer(('127.0.0.1', 0), QuietHandler)
        server.app, server.is_tls = self.app, False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/api/routers?availability=1'
        try:
            with mock.patch.object(hub.Handler, 'admin_ok', return_value=False), mock.patch.object(hub, 'login_html', return_value='Login'):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url, timeout=5)
                self.assertEqual(error.exception.code, 401)
            profile = {'username': 'viewer', 'is_owner': False, 'router_ids': ['b'], 'router_access': {'b': ['A']}}
            with mock.patch.object(hub.Handler, 'admin_ok', return_value=True), \
                    mock.patch.object(hub.Handler, 'current_account_profile', return_value=profile):
                with urllib.request.urlopen(url, timeout=5) as response:
                    result = json.load(response)
                self.assertEqual([router['id'] for router in result['routers']], ['b'])
                self.assertEqual(result['availability']['points'][-1][1:], [0, 1])
                with urllib.request.urlopen(url.split('?')[0], timeout=5) as response:
                    self.assertNotIn('availability', json.load(response), 'ordinary polls stay small')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
