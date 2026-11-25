"""Real HTTP/SQLite delivery and acknowledgement of router endpoint changes."""
import json
import urllib.request
import urllib.error
from unittest import mock
import hub_http_fixture as base

hub = base.hub


class HostCommands(base.HubHttpFixture):
    def beat(self, router='main', result=None, **extra):
        payload = {'id': router, 'xray': 'stopped', 'xray_control': 1, 'vps_host_control': 1,
                   'vps_host': 'old.example', 'vps_host_mode': 'manual', **extra}
        if result:
            payload['xray_control_result'] = result
        raw, _ = self.request('/api/heartbeat', payload, cookie=False, agent=True)
        return json.loads(raw)

    def post_host(self, host='new.example', mode='manual', request_id='a'*32, **headers):
        url = f'http://127.0.0.1:{self.server.server_port}/api/router/main/vps-host'
        body = json.dumps({'vps_host': host, 'vps_host_mode': mode, 'request_id': request_id}).encode()
        headers = {'Cookie': 'owrt_remote_session=session-test', 'Content-Type': 'application/json',
                   'X-VPS-Host-Request': '1', **headers}
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=5) as response:
            self.assertEqual(response.status, 202)
            return json.load(response)

    def row(self):
        with self.app.conn() as conn:
            return hub.row_to_router(hub.get_router(conn, 'main'))

    def result(self, **extra):
        return {'id': 'a'*32, 'action': 'set-host', 'status': 'succeeded', 'rc': 0, 'xray': 'running',
                'vps_host': 'new.example', 'vps_host_mode': 'manual', 'message': 'Process verified', **extra}

    def test_no_ssh_stopped_tunnel_delivery_only_to_selected_router_and_verified_ack(self):
        self.beat()
        with mock.patch.object(hub.Handler, 'run_router_ssh_script', side_effect=AssertionError('No SSH')):
            queued = self.post_host()['router']
            self.assertEqual(queued['vps_host'], 'hub.example')
            self.assertEqual(queued['vps_host_update']['status'], 'queued')
            self.assertIsNone(self.beat('other')['xray_command'])
            first = self.beat()['xray_command']
            self.assertEqual(first['parameters'], {'vps_host': 'new.example', 'vps_host_mode': 'manual'})
            self.assertEqual(self.beat()['xray_command'], first)
            self.beat(result=self.result(status='running', vps_host='old.example', xray='stopped'))
            self.assertEqual(self.row()['vps_host'], 'hub.example')
            reply = self.beat(result=self.result(), xray='running', vps_host='new.example')
            self.assertEqual(reply['router']['vps_host'], 'new.example')
            self.assertEqual(self.row()['vps_host_update']['status'], 'succeeded')
            self.assertIsNone(reply['xray_command'])

    def test_old_agent_validation_csrf_and_wrong_origin(self):
        self.beat(vps_host_control=0)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post_host()
        self.assertEqual(error.exception.code, 400)
        self.beat()
        for params in ({'host': 'https://bad'}, {'mode': 'invalid'}, {'X-VPS-Host-Request': ''},
                       {'Origin': 'https://evil.invalid'}, {'Cookie': ''}):
            with self.assertRaises(urllib.error.HTTPError):
                self.post_host(**params)
        self.assertFalse(self.row()['vps_host_update'])

    def test_idempotence_shared_xray_queue_and_restart_persistence(self):
        self.beat(); self.post_host(); self.post_host()
        with self.app.conn() as conn:
            self.assertEqual(conn.execute('select count(*) from router_xray_commands').fetchone()[0], 1)
            with self.assertRaisesRegex(ValueError, 'Предыдущая команда'):
                hub.xray_control_enqueue(conn, 'main', 'restart', 'b'*32)
        with self.assertRaises(urllib.error.HTTPError):
            self.post_host(host='different.example')
        self.server.app = self.app = hub.App(self.app.db_path, 'session-test', 'agent-test', '')
        self.assertEqual(self.row()['vps_host_update']['status'], 'queued')
        self.assertEqual(self.beat()['xray_command']['id'], 'a'*32)

    def test_wrong_result_cannot_fake_success_or_update_host(self):
        for change in ({'vps_host': 'different.example'}, {'xray': 'stopped'}, {'rc': 23}):
            with self.subTest(change=change):
                self.beat(); self.post_host(request_id='a'*32); self.beat()
                self.beat(result=self.result(**change), vps_host='new.example', xray='running')
                self.assertEqual(self.row()['vps_host'], 'hub.example')
                self.assertEqual(self.row()['vps_host_update']['status'], 'failed')
                with self.app.conn() as conn:
                    conn.execute('delete from router_xray_commands')

    def test_auto_mode_does_not_receive_old_endpoint_while_applying(self):
        self.beat(vps_host_mode='auto'); self.post_host(mode='auto')
        reply = self.beat(vps_host_mode='auto')
        self.assertNotIn('vps_host', reply['hub_update'])
        reply = self.beat(result=self.result(vps_host_mode='auto'), vps_host_mode='auto',
                          vps_host='new.example', xray='running')
        self.assertEqual(reply['hub_update']['vps_host'], 'new.example')

    def test_late_previous_result_does_not_replace_new_operation_status(self):
        self.beat(); self.post_host(); self.beat()
        with mock.patch.object(hub, 'now_ts', return_value=hub.now_ts()+181):
            self.beat()
        self.post_host(host='second.example', request_id='b'*32)
        self.beat()
        self.beat(result=self.result(status='failed', rc=1))
        self.assertEqual(self.row()['vps_host_update']['id'], 'b'*32)
        self.assertEqual(self.row()['vps_host_update']['status'], 'delivered')

    def test_legacy_stale_status_cannot_undo_recent_confirmed_host_and_capability(self):
        self.beat(); self.post_host(); self.beat()
        self.beat(result=self.result(), vps_host='new.example', xray='running')
        stale = self.beat(vps_host_control=0, vps_host='old.example')
        self.assertTrue(stale['heartbeat_ignored'])
        self.assertEqual(stale['hub_update'], {})
        self.assertEqual(self.row()['status']['vps_host'], 'new.example')
        self.assertEqual(self.row()['status']['vps_host_control'], 1)
        # A real change from the current worker is still observed; no indefinite pinning.
        fresh = self.beat(result=self.result(), vps_host='changed-locally.example')
        self.assertFalse(fresh['heartbeat_ignored'])
        self.assertEqual(self.row()['status']['vps_host'], 'changed-locally.example')

    def test_ordered_heartbeat_rejects_late_and_duplicate_samples(self):
        boot = '11111111-1111-4111-8111-111111111111'
        self.beat(agent_boot_id=boot, sample_uptime_ms=1000, vps_host='old.example')
        self.beat(agent_boot_id=boot, sample_uptime_ms=2000, vps_host='new.example')
        for stamp in (1000, 2000):
            reply = self.beat(agent_boot_id=boot, sample_uptime_ms=stamp, vps_host='old.example', vps_host_control=0)
            self.assertTrue(reply['heartbeat_ignored'])
            self.assertEqual(self.row()['status']['vps_host'], 'new.example')
        self.beat(agent_boot_id=boot, sample_uptime_ms=3000, vps_host='local-change.example')
        self.assertEqual(self.row()['status']['vps_host'], 'local-change.example')

    def test_reboot_accepts_reset_clock_then_rejects_previous_boot_and_old_process(self):
        old = '11111111-1111-4111-8111-111111111111'
        new = '22222222-2222-4222-8222-222222222222'
        self.beat(agent_boot_id=old, sample_uptime_ms=10000)
        reply = self.beat(agent_boot_id=new, sample_uptime_ms=100, vps_host='after-reboot.example')
        self.assertFalse(reply['heartbeat_ignored'])
        self.assertTrue(self.beat(agent_boot_id=old, sample_uptime_ms=11000)['heartbeat_ignored'])
        self.assertTrue(self.beat(vps_host_control=0)['heartbeat_ignored'])
        self.assertEqual(self.row()['status']['vps_host'], 'after-reboot.example')
        # An intentional rollback to an older agent remains possible after the grace period.
        with mock.patch.object(hub, 'now_ts', return_value=hub.now_ts()+181):
            self.assertFalse(self.beat(vps_host_control=0)['heartbeat_ignored'])

    def test_late_snapshot_cannot_deliver_or_finish_a_new_command(self):
        boot = '11111111-1111-4111-8111-111111111111'
        self.beat(agent_boot_id=boot, sample_uptime_ms=2000)
        self.post_host()
        with mock.patch.object(hub, 'maybe_restart_vps_xray_after_wan_reconnect') as reconnect:
            late = self.beat(agent_boot_id=boot, sample_uptime_ms=1000, result=self.result(), event='wan-reconnect')
            reconnect.assert_not_called()
        self.assertIsNone(late['xray_command'])
        self.assertEqual(self.row()['vps_host_update']['status'], 'queued')
        self.assertEqual(self.beat(agent_boot_id=boot, sample_uptime_ms=3000)['xray_command']['id'], 'a'*32)

    def test_undelivered_expiry_does_not_apply_and_late_result_can_confirm_delivery(self):
        self.beat(); self.post_host()
        with mock.patch.object(hub, 'now_ts', return_value=hub.now_ts()+181):
            self.assertIsNone(self.beat()['xray_command'])
            self.assertEqual(self.row()['vps_host_update']['status'], 'expired')
        self.post_host(request_id='b'*32)
        self.beat()
        with mock.patch.object(hub, 'now_ts', return_value=hub.now_ts()+181):
            self.beat(result=self.result(id='b'*32), vps_host='new.example', xray='running')
            self.assertEqual(self.row()['vps_host_update']['status'], 'succeeded')
