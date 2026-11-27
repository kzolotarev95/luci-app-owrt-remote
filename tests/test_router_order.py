"""Saved per-account card ordering through actual authenticated Hub HTTP/SQLite."""
import json
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from hub_http_fixture import HubHttpFixture, hub


class RouterOrderTests(HubHttpFixture):
    def setUp(self):
        super().setUp()
        # Finish fixture authentication schema migration before concurrent reads.
        hub.load_auth()

    def move(self, key='other', target='main', placement='before', headers=None, cookie=True):
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/api/routers/order',
            data=json.dumps({'id': key, 'target': target, 'placement': placement,
                             'base_order': ['main', 'other']}).encode(),
            headers={'Content-Type': 'application/json', 'X-Router-Order': '1',
                     **({'Cookie': 'owrt_remote_session=session-test'} if cookie else {}), **(headers or {})})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def snapshot(self):
        return json.loads(self.request('/api/routers')[0])

    def test_move_persists_after_heartbeat_rename_and_new_connection(self):
        self.assertTrue(self.snapshot()['router_order_supported'])
        self.assertEqual(self.snapshot()['router_order'], [])
        self.assertEqual(self.move()['router_order'], ['other', 'main'])
        with self.app.conn() as conn:
            conn.execute("update routers set name='A changed name' where id='main'")
            hub.heartbeat(conn, {'id': 'main', 'name': 'Agent name', 'xray': 'running'})
        result = self.snapshot()
        self.assertEqual(result['router_order'], ['other', 'main'])
        self.assertEqual({row['id']: row['display_order'] for row in result['routers']}, {'other': 0, 'main': 1})
        self.assertEqual(self.move('other', 'main', 'after')['router_order'], ['main', 'other'])

    def test_delegated_order_is_private_and_does_not_require_router_management(self):
        self.move()
        profile = {'username': 'viewer', 'is_owner': False, 'router_ids': ['main', 'other'],
                   'router_flags': {'main': ['A'], 'other': ['A']}}
        with mock.patch.object(hub.Handler, 'current_account_profile', return_value=profile):
            self.assertEqual(self.snapshot()['router_order'], [])
            self.assertEqual(self.move('main', 'other', 'after')['router_order'], ['other', 'main'])
            self.move('main', 'other', 'before')
            self.assertEqual(self.snapshot()['router_order'], ['main', 'other'])
        self.assertEqual(self.snapshot()['router_order'], ['other', 'main'])

    def test_hidden_router_is_not_exposed_or_movable(self):
        self.move()
        profile = {'username': 'limited', 'is_owner': False, 'router_ids': ['main'], 'router_flags': {'main': ['A']}}
        with mock.patch.object(hub.Handler, 'current_account_profile', return_value=profile):
            self.assertEqual(self.snapshot()['router_order'], [])
            self.assertEqual([row['id'] for row in self.snapshot()['routers']], ['main'])
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.move()
            self.assertEqual(error.exception.code, 404)

    def test_unauthenticated_agent_and_cross_origin_cannot_reorder(self):
        for headers, cookie, status in [({}, False, 401), ({'Authorization': 'Bearer agent-test'}, False, 401),
                ({'Origin': 'https://evil.example'}, True, 403), ({'X-Router-Order': ''}, True, 403),
                ({'Content-Type': 'text/plain'}, True, 403)]:
            with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError) as error:
                self.move(headers=headers, cookie=cookie)
            self.assertEqual(error.exception.code, status)
        self.assertEqual(self.snapshot()['router_order'], [])

    def test_invalid_move_does_not_change_order(self):
        self.move()
        for key, target, placement in [('main', 'main', 'before'), ('unknown', 'other', 'after'),
                                        ('main', 'other', 'invalid'), ([], 'main', 'before')]:
            with self.subTest(key=key), self.assertRaises(urllib.error.HTTPError):
                self.move(key, target, placement)
        self.assertEqual(self.snapshot()['router_order'], ['other', 'main'])

    def test_concurrent_moves_keep_all_ids_and_groups(self):
        with self.app.conn() as conn:
            hub.upsert_router(conn, {'id': 'third', 'name': 'Third', 'vps_host': 'hub.example'})
            conn.execute("update routers set group_id='original' where id='main'")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda n: self.move('third' if n % 2 else 'other', 'main', 'before'), range(12)))
        self.assertTrue(all(len(row['router_order']) == 3 for row in results))
        self.assertEqual(set(self.snapshot()['router_order']), {'main', 'other', 'third'})
        with self.app.conn() as conn:
            self.assertEqual(hub.get_router(conn, 'main')['group_id'], 'original')

    def test_deleted_router_loses_position_before_recreation(self):
        self.move()
        with self.app.conn() as conn:
            hub.delete_router(conn, 'other')
            hub.upsert_router(conn, {'id': 'other', 'name': 'Fresh', 'vps_host': 'hub.example'})
        self.assertEqual(self.snapshot()['router_order'], ['main'])
        row = next(row for row in self.snapshot()['routers'] if row['id'] == 'other')
        self.assertIsNone(row['display_order'])
