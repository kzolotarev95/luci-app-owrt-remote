"""Deletion, late heartbeat and same-ID recreation with real SQLite/HTTP."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import urllib.error
from unittest import mock

from hub_http_fixture import HubHttpFixture, hub


class RouterDeletionTests(HubHttpFixture):
    def setUp(self):
        super().setUp()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(hub, 'reload_vps_xray', return_value={'ok': True}).start()
        mock.patch.object(hub, 'notify_router_online').start()
        self.media = Path(self.temp.name) / 'media'
        mock.patch.object(hub, 'ROUTER_NOTES_MEDIA_DIR', self.media).start()
        with self.app.conn() as conn:
            self.old = dict(hub.get_router(conn, 'main'))
            hub.heartbeat(conn, {'id': 'main', 'name': 'Old agent name', 'model': 'Old model',
                'xray': 'running', 'tachyon': {'checked': True, 'installed': True,
                'mode': 'standalone', 'port': 1680, 'version': '2.0.0-rc.5', 'available': True}})
            conn.execute("update routers set custom_name='Old custom name',notes='Old note',group_id='old',vps_host_update_json='{}' where id='main'")
            conn.execute("insert into router_xray_commands(id,router_id,action,created_at) values('old-command','main','restart',1)")
            conn.execute("insert into router_availability values(60,'main',60,1)")
            conn.execute("insert into router_availability values(60,'other',60,0)")
        for router_id in ['main', 'other']:
            folder = self.media / router_id / 'note'
            folder.mkdir(parents=True)
            (folder / 'image.png').write_bytes(b'private image')

    def delete(self):
        raw, _ = self.request('/api/router/main/delete', {}, method='POST')
        return json.loads(raw)

    def beat(self, **values):
        raw, _ = self.request('/api/heartbeat', {'id': 'main', 'name': 'Old agent name',
            'model': 'Old model', 'xray': 'running', **values}, cookie=False, agent=True)
        return json.loads(raw)

    def create(self):
        raw, _ = self.request('/api/router', {'id': 'main', 'name': 'Fresh router', 'entry_port': 18101})
        return json.loads(raw)['router']

    def assert_empty(self, router):
        self.assertIsNone(router['last_seen'])
        self.assertFalse(router['online'])
        self.assertEqual(router['status'], {})
        self.assertEqual(router['custom_name'], '')
        self.assertEqual(router['group_id'], '')
        self.assertFalse(router['has_notes'])
        self.assertEqual(router['vps_host_update'], {})
        self.assertNotIn('tachyon', router)

    def test_delete_erases_instance_data_and_ignores_late_heartbeats(self):
        self.assertTrue(self.delete()['deleted'])
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: self.beat(), range(12)))
        self.assertTrue(all(result['heartbeat_ignored'] and result['hub_update'] == {} for result in responses))
        raw, _ = self.request('/api/routers')
        self.assertNotIn('main', [router['id'] for router in json.loads(raw)['routers']])
        with self.app.conn() as conn:
            row = hub.get_router(conn, 'main')
            self.assertTrue(hub.router_deleted(row))
            self.assert_empty(hub.row_to_router(row))
            self.assertEqual(row['vless_uuid'], '')
            self.assertEqual(row['tachyon_entry_port'], 0)
            self.assertEqual(conn.execute("select count(*) from router_xray_commands where router_id='main'").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from router_availability where router_id='main'").fetchone()[0], 0)
            self.assertEqual(conn.execute("select count(*) from router_availability where router_id='other'").fetchone()[0], 1)
            hub.init_db(conn)
            self.assertEqual(hub.get_router(conn, 'main')['ssh_vless_uuid'], '')
        self.assertFalse((self.media / 'main').exists())
        self.assertTrue((self.media / 'other/note/image.png').is_file())
        self.assertFalse(self.delete()['deleted'], 'delete is idempotent')

    def test_recreated_id_is_empty_and_old_agent_cannot_fill_it(self):
        self.delete()
        fresh = self.create()
        self.assert_empty(fresh)
        self.assertEqual(fresh['name'], 'Fresh router')
        self.assertNotEqual(fresh['vless_uuid'], self.old['vless_uuid'])
        self.assertNotEqual(fresh['ssh_vless_uuid'], self.old['ssh_vless_uuid'])
        for identity in [None, '', self.old['vless_uuid'], hub.router_agent_identity(self.old), {'invalid': True}]:
            result = self.beat(agent_identity=identity)
            self.assertTrue(result['heartbeat_ignored'])
            self.assertNotIn('vless_uuid', result['router'])
            self.assertNotIn('ssh_vless_uuid', result['router'])
            with self.app.conn() as conn:
                self.assert_empty(hub.row_to_router(hub.get_router(conn, 'main')))
        current = self.beat(agent_identity=hub.router_agent_identity(fresh), name='Fresh agent', model='New model')
        self.assertFalse(current['heartbeat_ignored'])
        self.assertEqual(current['router']['status']['model'], 'New model')
        self.assertNotIn('agent_identity', current['router']['status'])
        self.assertTrue(current['router']['online'])

    def test_first_valid_heartbeat_after_recreate_reloads_new_tunnel_credentials(self):
        self.delete()
        fresh = self.create()
        with self.app.conn() as conn:
            result = hub.heartbeat(conn, {'id': 'main', 'agent_identity': hub.router_agent_identity(fresh)})
            self.assertTrue(result['_xray_reload_required'])
            result = hub.heartbeat(conn, {'id': 'main', 'agent_identity': hub.router_agent_identity(fresh)})
            self.assertFalse(result['_xray_reload_required'], 'normal polling must not keep restarting the server')

    def test_old_tombstone_from_previous_hub_is_reset_on_explicit_create(self):
        with self.app.conn() as conn:
            conn.execute("update routers set deleted_at=1 where id='main'")
        fresh = self.create()
        self.assert_empty(fresh)
        self.assertNotEqual(fresh['vless_uuid'], self.old['vless_uuid'])
        self.assertTrue(self.beat()['heartbeat_ignored'])
        self.assertFalse((self.media / 'main').exists())

    def test_existing_active_router_accepts_legacy_agents(self):
        self.assertFalse(self.beat()['heartbeat_ignored'])

    def test_recreate_always_rotates_credentials_even_if_old_values_are_submitted(self):
        self.delete()
        with self.app.conn() as conn:
            fresh = hub.upsert_router(conn, {'id': 'main', 'vless_uuid': self.old['vless_uuid'],
                'ssh_vless_uuid': self.old['ssh_vless_uuid']})
            self.assertNotEqual(fresh['vless_uuid'], self.old['vless_uuid'])
            self.assertNotEqual(fresh['ssh_vless_uuid'], self.old['ssh_vless_uuid'])

    def test_deleted_and_recreated_instance_does_not_deliver_new_commands_to_old_agent(self):
        self.delete()
        fresh = self.create()
        with self.app.conn() as conn:
            conn.execute("insert into router_xray_commands(id,router_id,action,created_at) values('new-command','main','start',?)", (hub.now_ts(),))
            self.assertIsNone(hub.xray_control_exchange(conn, 'main', {'xray_control': 1}))
            self.assertEqual(hub.xray_control_exchange(conn, 'main', {'xray_control': 1,
                'agent_identity': hub.router_agent_identity(fresh)})['id'], 'new-command')

    def test_delete_clears_sessions_cached_pages_and_old_access_grants(self):
        auth = hub.load_auth()
        auth['delegated_users'] = [{'id': 'u', 'username': 'viewer', 'enabled': True,
            'password': {'hash': 'test-only'}, 'router_flags': {'main': ['G'], 'other': ['A']}}]
        hub.save_auth_state(auth)
        session = {'router_id': 'main', 'alive': True, 'lock': threading.Lock()}
        with mock.patch.dict(hub.SSH_HTTP_SESSIONS, {'s': session}, clear=True), \
             mock.patch.dict(hub.STATIC_CACHE, {}, clear=True), mock.patch.object(hub, 'STATIC_CACHE_BYTES', 0):
            for rid in ['main', 'tachyon:main', 'other']:
                hub.static_cache_put(hub.static_cache_key(rid, '/asset.js'), 200, b'old', 'text/javascript', [])
            self.delete()
            self.assertFalse(session['alive'])
            self.assertNotIn('s', hub.SSH_HTTP_SESSIONS)
            self.assertEqual(set(hub.STATIC_CACHE), {hub.static_cache_key('other', '/asset.js')})
            self.assertEqual(hub.STATIC_CACHE_BYTES, 3)
        grants = hub.load_auth()['delegated_users'][0]['router_flags']
        self.assertNotIn('main', grants)
        self.assertIn('other', grants)

    def test_delete_requires_owner(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/router/main/delete', {}, cookie=False)
        self.assertIn(error.exception.code, [401, 403])
        with self.app.conn() as conn:
            self.assertFalse(hub.router_deleted(hub.get_router(conn, 'main')))

    def test_initial_registration_in_flight_cannot_overwrite_recreated_instance(self):
        original = hub.upsert_router
        def interleaved(conn, values):
            old = original(conn, values)
            hub.delete_router(conn, values['id'])
            original(conn, {'id': values['id'], 'name': 'New instance'})
            return old
        with self.app.conn() as conn, mock.patch.object(hub, 'upsert_router', side_effect=interleaved):
            result = hub.heartbeat(conn, {'id': 'race', 'model': 'Old data', 'name': 'Old instance'})
            self.assertTrue(result['_heartbeat_ignored'])
            self.assert_empty(result)
            self.assertEqual(result['name'], 'New instance')

    def test_failed_cleanup_returns_explicit_error_without_reporting_success(self):
        with mock.patch.object(hub, 'clear_router_instance_data', side_effect=OSError('media unavailable')):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.delete()
        self.assertEqual(error.exception.code, 500)
        self.assertIn('media unavailable', json.loads(error.exception.read())['error'])
        with self.app.conn() as conn:
            self.assertFalse(hub.router_deleted(hub.get_router(conn, 'main')))
