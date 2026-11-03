"""Sequential updates: isolated Hub, fake SSH, and real shell task execution."""
import base64
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
STATE = tempfile.TemporaryDirectory(prefix='owrt-router-update-tests-')
spec = importlib.util.spec_from_file_location('router_update_hub', ROOT / 'vps/owrt-remote-hub.py')
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {'OWRT_REMOTE_STATE_DIR': STATE.name}):
    spec.loader.exec_module(hub)


def b64(value):
    return base64.b64encode(value.encode()).decode()


class UpdatesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=STATE.name)
        self.addCleanup(self.temp.cleanup)
        self.app = hub.App(Path(self.temp.name) / 'hub.db', 'owner-session', 'agent', '')
        self.manager = self.app.router_updates
        self.manager.POLL_SECONDS = .001
        self.manager.ROUTER_TIMEOUT = .02
        self.manager.CONNECTION_TIMEOUT = .015
        self.manager.LAUNCH_TIMEOUT = .01
        self.manager.SILENCE_TIMEOUT = .015
        self.manager.STOP_TIMEOUT = .01
        self.manager.REBOOT_TIMEOUT = .025
        hub.save_auth_state({'username': 'owner', 'password': hub.password_digest('hub-password')})
        hub.save_sessions([])
        with self.app.conn() as conn:
            for index, name in enumerate(['Old name', 'Second router']):
                row = hub.upsert_router(conn, {'id': ['main', 'node-2'][index], 'name': name,
                                             'entry_port': 18080 + index, 'ssh_entry_port': 19080 + index})
                conn.execute('update routers set status_json=? where id=?',
                             (json.dumps({'name': 'Советская 30' if index == 0 else name}), row['id']))
        self.addCleanup(self.manager.stop)

    def finish(self):
        self.manager.thread.join(timeout=3)
        self.assertFalse(self.manager.thread.is_alive())
        return self.manager.snapshot()

    def test_reboot_requires_new_boot_and_runs_sequentially_without_installer(self):
        calls, polls = [], {}
        old_boot, new_boot = 'a' * 8 + '-1111-1111-1111-' + 'a' * 12, 'b' * 8 + '-2222-2222-2222-' + 'b' * 12
        def ssh(row, script, password):
            calls.append((row['id'], script, password))
            if 'OWRT_UPDATE_READY=1' in script:
                return 'OWRT_UPDATE_READY=1\nOWRT_UPDATE_BOOT_ID=' + old_boot + '\nOWRT_UPDATE_NAME_TEXT=АНТОН ВПН\n'
            if 'OWRT_REBOOT_RUNNER' in script:
                return 'OWRT_UPDATE_STARTED=1\n'
            polls[row['id']] = polls.get(row['id'], 0) + 1
            # Neither rc=0 nor the same working SSH session proves a reboot.
            return 'OWRT_UPDATE_BOOT_ID=' + (old_boot if polls[row['id']] == 1 else new_boot) + '\nOWRT_UPDATE_RC=0\n'
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start_reboot('shared-secret', 'request-reboot-success')
            job = self.finish()
        self.assertEqual(job['operation'], 'reboot')
        self.assertEqual([item['state'] for item in job['items']], ['success', 'success'])
        self.assertTrue(all(item['name'] == 'АНТОН ВПН' for item in job['items']))
        self.assertEqual([row for row, _, _ in calls], ['main'] * 4 + ['node-2'] * 4)
        self.assertTrue(all(password == 'shared-secret' for _, _, password in calls))
        self.assertNotIn('shared-secret', json.dumps(job))
        self.assertTrue(all('command.sh' not in script for _, script, _ in calls))

    def test_reboot_disconnect_never_resends_and_only_new_boot_confirms_success(self):
        launches, probes = [], 0
        def ssh(row, script, password):
            nonlocal probes
            if 'OWRT_UPDATE_READY=1' in script:
                return 'OWRT_UPDATE_READY=1\nOWRT_UPDATE_BOOT_ID=aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa\n'
            if 'OWRT_REBOOT_RUNNER' in script:
                launches.append(row['id'])
                raise RuntimeError('Connection closed by remote host')
            probes += 1
            if probes == 1:
                raise RuntimeError('SSH: нет ответа за 10 секунд')
            return 'OWRT_UPDATE_BOOT_ID=bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb\n'
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start_reboot('', 'request-reboot-disconnect')
            job = self.finish()
        self.assertEqual(launches, ['main', 'node-2'])
        self.assertTrue(all(item['state'] == 'success' for item in job['items']))

    def test_reboot_unknown_or_rejected_continues_and_does_not_claim_success(self):
        for failure in ['unchanged_boot', 'reboot_rc', 'wrong_password', 'missing_tool']:
            with self.subTest(failure=failure):
                launches = []
                def ssh(row, script, password):
                    if 'OWRT_UPDATE_READY=1' in script:
                        if row['id'] == 'main' and failure == 'wrong_password':
                            raise RuntimeError('root@127.0.0.1: Permission denied (publickey,password).')
                        if row['id'] == 'main' and failure == 'missing_tool':
                            return 'OWRT_UPDATE_STATE=not_started\nOWRT_UPDATE_ERROR=Нет reboot; перезагрузка не запускалась\n'
                        return 'OWRT_UPDATE_READY=1\nOWRT_UPDATE_BOOT_ID=aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa\n'
                    if 'OWRT_REBOOT_RUNNER' in script:
                        launches.append(row['id'])
                        return 'OWRT_UPDATE_STARTED=1\n'
                    if row['id'] == 'main':
                        return 'OWRT_UPDATE_BOOT_ID=aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa\nOWRT_UPDATE_RC=' + ('1' if failure == 'reboot_rc' else '0') + '\nOWRT_UPDATE_OUTPUT_TEXT=\nreboot denied'
                    return 'OWRT_UPDATE_BOOT_ID=bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb\n'
                with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
                    self.manager.start_reboot('shared-secret', 'request-reboot-' + failure)
                    job = self.finish()
                self.assertEqual(job['items'][0]['state'], 'unknown' if failure == 'unchanged_boot' else 'error')
                self.assertEqual(job['items'][1]['state'], 'success')
                self.assertEqual(launches, ['node-2'] if failure in {'wrong_password', 'missing_tool'} else ['main', 'node-2'])
                if failure == 'wrong_password':
                    self.assertIn('Общий SSH-пароль не принят', job['items'][0]['message'])
                if failure == 'reboot_rc':
                    self.assertIn('rc=1', job['items'][0]['message'])

    def test_reboot_cancel_duplicate_and_conflicting_queue(self):
        entered, release = threading.Event(), threading.Event()
        launches = []
        def ssh(row, script, password):
            if 'OWRT_UPDATE_READY=1' in script:
                return 'OWRT_UPDATE_READY=1\nOWRT_UPDATE_BOOT_ID=aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa\n'
            if 'OWRT_REBOOT_RUNNER' in script:
                launches.append(row['id']); entered.set(); release.wait(2)
                return 'OWRT_UPDATE_STARTED=1\n'
            return 'OWRT_UPDATE_BOOT_ID=aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa\n'
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            task = self.manager.start_reboot('', 'request-reboot-cancel')
            self.assertTrue(entered.wait(2))
            self.assertEqual(task, self.manager.start_reboot('', 'request-reboot-cancel'))
            with self.assertRaises(RuntimeError):
                self.manager.start('echo installer', '', 'request-update-conflict')
            with self.assertRaises(ValueError):
                self.manager.start('echo installer', '', 'request-reboot-cancel')
            self.manager.cancel(task)
            release.set()
            job = self.finish()
        self.assertEqual(launches, ['main'])
        self.assertEqual([item['state'] for item in job['items']], ['unknown', 'skipped'])
        self.assertIn('reboot не отменяется', job['items'][0]['message'])

    def test_sequential_names_failures_and_no_password_in_snapshot(self):
        calls = []
        def ssh(row, script, password):
            if 'OWRT_UPDATE_READY=1' in script: return 'OWRT_UPDATE_READY=1\n'
            calls.append((row['id'], script, password))
            if 'exec setsid /bin/sh' in script:
                return 'OWRT_UPDATE_STARTED=1\nOWRT_UPDATE_NAME=' + b64('Советская 30' if row['id'] == 'main' else 'node-2')
            if 'OWRT_UPDATE_OUTPUT_TEXT=' in script:
                return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=' + ('0' if row['id'] == 'main' else '5') + '\nOWRT_UPDATE_OUTPUT=' + b64('ssh-secret\nhub_token=hidden\nCompleted')
            return ''
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start('wget -O - "https://repo.example/install.sh" | sh', 'ssh-secret', 'request-1234567890')
            job = self.finish()
        self.assertEqual([x[0] for x in calls], ['main'] * 3 + ['node-2'] * 3)
        self.assertTrue(all(x[2] == 'ssh-secret' for x in calls))
        self.assertEqual(job['items'][0]['name'], 'Советская 30')
        self.assertEqual([x['state'] for x in job['items']], ['success', 'error'])
        self.assertNotIn('ssh-secret', json.dumps(job))
        self.assertNotIn('hub_token=hidden', json.dumps(job))
        self.assertNotIn('command', job)
        self.assertIsNone(getattr(self.manager.thread, '_target', None))

    def test_launch_disconnect_polls_existing_task_without_reexecution(self):
        launches = []
        def ssh(row, script, password):
            if 'OWRT_UPDATE_READY=1' in script: return 'OWRT_UPDATE_READY=1\n'
            if 'exec setsid /bin/sh' in script:
                launches.append(row['id'])
                raise RuntimeError('connection closed after acceptance')
            return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=0\n'
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start('echo install', '', 'request-1234567891')
            job = self.finish()
        self.assertEqual(launches, ['main', 'node-2'])
        self.assertTrue(all(x['state'] == 'success' for x in job['items']))

    def test_disconnected_router_does_not_block_remaining_queue_or_claim_success(self):
        launches = []
        def ssh(row, script, password):
            if 'OWRT_UPDATE_READY=1' in script: return 'OWRT_UPDATE_READY=1\n'
            if 'exec setsid /bin/sh' in script:
                launches.append(row['id'])
                return 'OWRT_UPDATE_STARTED=1\n'
            raise RuntimeError('temporary disconnect')
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start('echo install', '', 'request-1234567892')
            job = self.finish()
        self.assertEqual(launches, ['main', 'node-2'])
        self.assertEqual([x['state'] for x in job['items']], ['unknown', 'unknown'])
        self.assertIn('SSH не отвечает', job['items'][0]['message'])
        self.assertIn('temporary disconnect', job['items'][0]['message'])

    def test_duplicate_click_and_cancel_stop_current_without_launching_next(self):
        entered, release = threading.Event(), threading.Event()
        def ssh(row, script, password):
            if 'OWRT_UPDATE_READY=1' in script: return 'OWRT_UPDATE_READY=1\n'
            if 'exec setsid /bin/sh' in script:
                entered.set(); release.wait(2)
                return 'OWRT_UPDATE_STARTED=1\n'
            if 'OWRT_UPDATE_CANCEL=1' in script: return 'OWRT_UPDATE_CANCEL=1\n'
            return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=130\nOWRT_UPDATE_REASON=cancelled\n'
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            task = self.manager.start('echo install', '', 'request-1234567893')
            self.assertTrue(entered.wait(2))
            self.assertEqual(task, self.manager.start('echo install', '', 'request-1234567893'))
            with self.assertRaises(RuntimeError):
                self.manager.start('echo install', '', 'request-1234567894')
            self.manager.cancel(task)
            self.assertEqual(self.manager.snapshot()['items'][1]['state'], 'skipped')
            release.set()
            job = self.finish()
        self.assertEqual([x['state'] for x in job['items']], ['cancelled', 'skipped'])

    def test_silent_and_dead_installers_explain_failure_and_continue(self):
        self.manager.CONNECTION_TIMEOUT = .2
        for first_state in ['running', 'dead']:
            with self.subTest(first_state=first_state):
                cancelled = []
                def ssh(row, script, password):
                    if 'OWRT_UPDATE_READY=1' in script: return 'OWRT_UPDATE_READY=1\n'
                    if 'exec setsid /bin/sh' in script: return 'OWRT_UPDATE_STARTED=1\n'
                    if 'OWRT_UPDATE_CANCEL=1' in script:
                        cancelled.append(row['id'])
                        return 'OWRT_UPDATE_CANCEL=1\n'
                    if row['id'] == 'node-2': return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=0\n'
                    if cancelled: return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=130\nOWRT_UPDATE_REASON=cancelled\n'
                    return 'OWRT_UPDATE_STATE=' + first_state + '\n'
                with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
                    self.manager.start('echo install', '', 'request-silence-' + first_state)
                    job = self.finish()
                self.assertEqual([x['state'] for x in job['items']], ['error', 'success'])
                self.assertIn('Нет нового вывода' if first_state == 'running' else 'без итогового кода', job['items'][0]['message'])

    def test_normalizes_any_http_script_url_without_changing_full_commands(self):
        full = 'wget -O - "https://repo.example/install.sh?v=$(date +%s)" | sh'
        self.assertEqual(hub.normalize_router_update_command(full), full)
        for url in ['https://repo.example/a.sh', 'https://mirror.example/install.sh?q=one&v=2',
                    "http://192.0.2.1:8080/a';touch${IFS}/tmp/sentinel;#.sh"]:
            command = hub.normalize_router_update_command(url)
            self.assertIn('download.sh', command)
            self.assertIn(hub.shlex.quote(url), command)
            self.assertIn('/bin/sh "$file"', command)
        with self.assertRaises(ValueError):
            hub.normalize_router_update_command('https://repo.example/path with space')

    def test_transport_timeout_kills_reaps_and_removes_askpass(self):
        askpass = Path(self.temp.name) / 'askpass'
        askpass.mkdir()
        (askpass/'askpass.sh').write_text('fake')
        process = mock.Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired('ssh', 10), ('', '')]
        process.poll.return_value = None
        with mock.patch.object(hub.Handler, 'prepare_ssh_askpass', return_value=askpass), \
                mock.patch.object(hub.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(RuntimeError, 'нет ответа за 10 секунд'):
                self.manager._ssh({'ssh_entry_port': 19080}, 'echo ready', 'secret')
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)
        self.assertIsNone(self.manager.transport)
        self.assertFalse(askpass.exists())

    def test_stop_interrupts_inflight_ssh_preflight_before_any_installation(self):
        entered, killed = threading.Event(), threading.Event()
        process = mock.Mock()
        process.returncode = -9
        process.poll.side_effect = lambda: -9 if killed.is_set() else None
        process.kill.side_effect = killed.set
        def communicate(*args, **kwargs):
            entered.set()
            killed.wait(2)
            return '', 'SSH connection closed'
        process.communicate.side_effect = communicate
        with mock.patch.object(hub.subprocess, 'Popen', return_value=process) as spawn:
            task = self.manager.start('echo install', '', 'request-cancel-transport')
            self.assertTrue(entered.wait(2))
            self.manager.cancel(task)
            job = self.finish()
        self.assertTrue(killed.is_set())
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual([x['state'] for x in job['items']], ['cancelled', 'skipped'])

    def test_missing_launch_and_router_side_timeout_do_not_hang_queue(self):
        for first_state in ['missing', 'timeout']:
            with self.subTest(first_state=first_state):
                def ssh(row, script, password):
                    if 'OWRT_UPDATE_READY=1' in script: return 'OWRT_UPDATE_READY=1\n'
                    if 'exec setsid /bin/sh' in script: return '' if first_state == 'missing' else 'OWRT_UPDATE_STARTED=1\n'
                    if 'OWRT_UPDATE_CANCEL=1' in script: return 'OWRT_UPDATE_CANCEL=1\n'
                    if row['id'] == 'node-2': return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=0\n'
                    if first_state == 'missing': return 'OWRT_UPDATE_STATE=missing\n'
                    return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_REASON=timeout\nOWRT_UPDATE_RC=124\n'
                with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
                    self.manager.start('echo install', '', 'request-result-' + first_state)
                    job = self.finish()
                self.assertNotEqual(job['items'][0]['state'], 'success')
                self.assertEqual(job['items'][1]['state'], 'success')

    def test_missing_tunnel_and_invalid_input(self):
        with self.app.conn() as conn:
            conn.execute('update routers set ssh_entry_port=0, entry_port=0')
        with mock.patch.object(self.manager, '_ssh') as ssh:
            self.manager.start('echo install', '', 'request-1234567895')
            job = self.finish()
            ssh.assert_not_called()
        self.assertTrue(all(x['state'] == 'skipped' for x in job['items']))
        for value in ['', 'a' * 8193, '\x00', None]:
            with self.assertRaises(ValueError):
                self.manager.start(value, '', 'request-1234567896')

    def test_failed_authentication_continues_without_sending_installer(self):
        calls = []
        def ssh(row, script, password):
            calls.append(script)
            raise RuntimeError('Permission denied, please try again.\nroot@127.0.0.1: Permission denied (publickey,password).')
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start('echo install', 'wrong-password', 'request-1234567888')
            job = self.finish()
        self.assertEqual(len(calls), 2)
        self.assertTrue(all('OWRT_UPDATE_READY' in script for script in calls))
        self.assertTrue(all(item['state'] == 'error' for item in job['items']))
        self.assertTrue(all('Общий SSH-пароль не принят' in item['message'] for item in job['items']))
        self.assertNotIn('строка с ключом или паролем скрыта', json.dumps(job, ensure_ascii=False))
        self.assertNotIn('wrong-password', json.dumps(job))

    def test_wrong_common_password_skips_installer_but_next_router_succeeds(self):
        launches = []
        def ssh(row, script, password):
            if row['id'] == 'main':
                raise RuntimeError('root@127.0.0.1: Permission denied (publickey,password,keyboard-interactive).')
            if 'OWRT_UPDATE_READY=1' in script: return 'OWRT_UPDATE_READY=1\n'
            if 'exec setsid /bin/sh' in script:
                launches.append(row['id'])
                return 'OWRT_UPDATE_STARTED=1\n'
            return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=0\n'
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start('echo install', 'common-secret', 'request-1234567889')
            job = self.finish()
        self.assertEqual(launches, ['node-2'])
        self.assertEqual([item['state'] for item in job['items']], ['error', 'success'])
        self.assertEqual(job['items'][0]['name'], 'Советская 30')
        self.assertIn('отличается от общего', job['items'][0]['message'])
        self.assertNotIn('common-secret', json.dumps(job))

    def test_authentication_diagnosis_preserves_other_errors_and_secret_filter(self):
        for response in ['Permission denied', 'Permission denied (password).', 'Authentication failed.']:
            with self.subTest(response=response):
                self.assertIn('Общий SSH-пароль не принят', self.manager._ssh_error(response, 'secret'))
        self.assertIn('SSH-ключ', self.manager._ssh_error('Permission denied (publickey).', ''))
        self.assertIn('не предлагает вход по паролю', self.manager._ssh_error('Permission denied (publickey).', 'secret'))
        for response in ['ssh command timeout after 20s', 'Connection refused', '/root/file: Permission denied']:
            with self.subTest(response=response):
                self.assertEqual(self.manager._ssh_error(response, 'secret'), response)
        safe = self.manager._ssh_error('Failed: password=secret\nConnection closed', 'secret')
        self.assertNotIn('secret', safe)
        self.assertIn('строка с ключом или паролем скрыта', safe)
        self.assertIn('Connection closed', safe)

    def test_missing_router_tool_is_a_confirmed_preflight_failure_and_next_runs(self):
        launches = []
        def ssh(row, script, password):
            if 'OWRT_UPDATE_READY=1' in script:
                name = 'АНТОН ВПН' if row['id'] == 'main' else 'Второй роутер'
                response = 'OWRT_UPDATE_NAME_TEXT=' + name + '\n'
                if row['id'] == 'main':
                    return response + ('OWRT_UPDATE_STATE=not_started\nOWRT_UPDATE_ERROR='
                                       'На роутере отсутствует утилита setsid. Команда обновления не запускалась.\n')
                return response + 'OWRT_UPDATE_READY=1\n'
            if 'exec setsid /bin/sh' in script:
                launches.append(row['id'])
                return 'OWRT_UPDATE_STARTED=1\n'
            return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=0\n'
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start('echo install', '', 'request-tools-missing')
            job = self.finish()
        self.assertEqual(launches, ['node-2'])
        self.assertEqual([item['state'] for item in job['items']], ['error', 'success'])
        self.assertEqual(job['items'][0]['name'], 'АНТОН ВПН')
        self.assertIn('setsid', job['items'][0]['message'])
        self.assertIn('не запускалась', job['items'][0]['message'])
        self.assertNotIn('не подтвержден', job['items'][0]['message'].lower())

    def test_staging_failure_does_not_poll_or_claim_uncertain_execution(self):
        calls = []
        def ssh(row, script, password):
            calls.append(script)
            if 'OWRT_UPDATE_READY=1' in script:
                return 'OWRT_UPDATE_READY=1\nOWRT_UPDATE_NAME_TEXT=АНТОН ВПН\n'
            if 'exec setsid /bin/sh' in script:
                return ('OWRT_UPDATE_STATE=not_started\nOWRT_UPDATE_ERROR='
                        'Подготовка задачи на роутере завершилась ошибкой, rc=1. Команда обновления не запускалась.\n')
            self.fail('A known prelaunch failure must not be polled or cancelled')
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start('echo install', '', 'request-stage-failure')
            job = self.finish()
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(item['state'] == 'error' for item in job['items']))

    def test_banner_timeout_explains_stage_without_claiming_a_wrong_password(self):
        launches = []
        def ssh(row, script, password):
            if row['id'] == 'main':
                raise RuntimeError('Connection timed out during banner exchange\nConnection to 127.0.0.1 port 19080 timed out')
            if 'OWRT_UPDATE_READY=1' in script: return 'OWRT_UPDATE_READY=1\n'
            if 'exec setsid /bin/sh' in script:
                launches.append(row['id'])
                return 'OWRT_UPDATE_STARTED=1\n'
            return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=0\n'
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start('echo install', 'secret', 'request-banner-timeout')
            job = self.finish()
        self.assertEqual(launches, ['node-2'])
        self.assertEqual([item['state'] for item in job['items']], ['error', 'success'])
        message = job['items'][0]['message']
        self.assertIn('SSH не прислал приветствие', message)
        self.assertIn('До проверки пароля дело не дошло', message)
        self.assertIn('Connection timed out during banner exchange', message)
        self.assertIn('Команда обновления не запускалась', message)

    def test_plain_text_protocol_does_not_parse_installer_output_as_metadata(self):
        output = 'строка\nOWRT_UPDATE_RC=0\nOWRT_UPDATE_STATE=done\n'
        fields = hub.router_update_fields('OWRT_UPDATE_STATE=running\nOWRT_UPDATE_OUTPUT_TEXT=\n' + output)
        self.assertEqual(fields['STATE'], 'running')
        self.assertNotIn('RC', fields)
        self.assertEqual(fields['OUTPUT'], output)
        fields = hub.router_update_fields('OWRT_UPDATE_NAME_TEXT=АНТОН ВПН\nOWRT_UPDATE_READY=1\n')
        self.assertEqual(fields['NAME'], 'АНТОН ВПН')

    def test_preflight_reads_uci_name_even_if_launch_fails(self):
        def ssh(row, script, password):
            if 'OWRT_UPDATE_READY=1' in script:
                return 'OWRT_UPDATE_NAME_TEXT=АНТОН ВПН\nOWRT_UPDATE_READY=1\n'
            return 'OWRT_UPDATE_STATE=not_started\nOWRT_UPDATE_ERROR=Недостаточно места; команда не запускалась\n'
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            self.manager.start('echo install', '', 'request-uci-name-read')
            job = self.finish()
        self.assertTrue(all(item['name'] == 'АНТОН ВПН' for item in job['items']))
        self.assertEqual([item['id'] for item in job['items']], ['main', 'node-2'])
        self.assertEqual(hub.router_update_name({'id': 'node-5', 'name': 'Роутер node-5'}),
                         'Имя роутера не получено')
        self.assertEqual(hub.router_update_name({'id': 'node-5', 'custom_name': 'Имя карандашом'}, 'АНТОН ВПН'),
                         'Имя карандашом')
        self.assertEqual(hub.router_update_name({'id': 'node-5', 'custom_name': 'Имя карандашом',
                                                'status_json': '{"name":"АНТОН ВПН"}'}), 'Имя карандашом')

    def test_rename_updates_open_queue_even_when_ssh_reports_old_name(self):
        class Quiet(hub.Handler):
            def log_message(self, *args): pass
        server = hub.HubHTTPServer(('127.0.0.1', 0), Quiet)
        server.app = self.app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        entered, release = threading.Event(), threading.Event()

        def request(path, body=None):
            headers = {'Cookie': hub.SESSION_COOKIE + '=owner-session',
                       'Content-Type': 'application/json'}
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                conn.request('POST' if body is not None else 'GET', path,
                             json.dumps(body) if body is not None else None, headers)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                return json.loads(response.read())
            finally:
                conn.close()

        def ssh(row, script, password):
            if 'OWRT_UPDATE_READY=1' in script:
                if row['id'] == 'main':
                    entered.set()
                    release.wait(3)
                return 'OWRT_UPDATE_NAME_TEXT=Старое имя UCI\nOWRT_UPDATE_READY=1\n'
            if 'OWRT_UPDATE_STARTED=1' in script:
                return 'OWRT_UPDATE_STARTED=1\nOWRT_UPDATE_NAME_TEXT=Старое имя UCI\n'
            return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=0\n'

        try:
            with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
                self.manager.start('echo install', '', 'request-rename-active')
                self.assertTrue(entered.wait(2))
                request('/api/router/main/rename', {'name': 'Новое имя карандашом'})
                # A heartbeat from the unchanged router must not undo the rename.
                with self.app.conn() as conn:
                    hub.heartbeat(conn, {'id': 'main', 'name': 'Старое имя UCI'})
                    self.assertEqual(hub.row_to_router(hub.get_router(conn, 'main'))['name'],
                                     'Новое имя карандашом')
                for finished in (False, True):
                    if finished:
                        release.set()
                        self.finish()
                    data = request('/api/router-updates')
                    self.assertEqual(data['job']['state'], 'finished' if finished else 'running')
                    self.assertEqual(data['routers'][0]['name'], 'Новое имя карандашом')
                    self.assertEqual(data['job']['items'][0]['name'], 'Новое имя карандашом')
                    self.assertEqual(data['job']['items'][0]['id'], 'main')
                # A new queue must also keep the Hub name after reading UCI over SSH.
                self.manager.start('echo install', '', 'request-rename-new-queue')
                self.assertEqual(self.finish()['items'][0]['name'], 'Новое имя карандашом')
        finally:
            release.set()
            if self.manager.thread:
                self.manager.thread.join(timeout=3)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_refresh_cannot_clear_running_or_newer_queue(self):
        entered, release = threading.Event(), threading.Event()
        def ssh(row, script, password):
            if 'OWRT_UPDATE_READY=1' in script:
                entered.set(); release.wait(2)
                return 'OWRT_UPDATE_READY=1\n'
            return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=0\n'
        with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
            task = self.manager.start('echo install', '', 'request-refresh-active')
            self.assertTrue(entered.wait(2))
            self.manager.clear_finished(task)
            self.assertEqual(self.manager.snapshot()['id'], task)
            release.set()
            self.finish()
        self.manager.clear_finished('stale-browser-job')
        self.assertEqual(self.manager.snapshot()['id'], task)
        self.manager.clear_finished(task)
        self.assertIsNone(self.manager.snapshot())

    def test_owner_only_api_csrf_and_page_default_hosting(self):
        class Quiet(hub.Handler):
            def log_message(self, *args): pass
        server = hub.HubHTTPServer(('127.0.0.1', 0), Quiet)
        server.app = self.app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, body=None, cookie='owner-session', csrf=None, origin=None):
            headers = {'Cookie': hub.SESSION_COOKIE + '=' + cookie}
            if body is not None: headers['Content-Type'] = 'application/json'
            if csrf is not None: headers['X-Router-Update-CSRF'] = csrf
            if origin is not None: headers['Origin'] = origin
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                conn.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers)
                response = conn.getresponse()
                return response.status, response.read().decode()
            finally: conn.close()
        try:
            self.assertEqual(request('/api/router-updates', cookie='')[0], 401)
            with mock.patch.object(hub.Handler, 'current_account_profile', return_value={'is_owner': False}):
                self.assertEqual(request('/api/router-updates')[0], 403)
                self.assertEqual(request('/router-updates/')[0], 403)
            status, page = request('/router-updates/')
            self.assertEqual(status, 200)
            self.assertIn(hub.RAW_REPO_BASE + '/install.sh', page)
            self.assertIn(self.manager.csrf, page)
            payload = {'command': 'echo install', 'ssh_password': '', 'request_id': 'request-1234567897'}
            self.assertEqual(request('/api/router-updates/start', payload)[0], 403)
            self.assertEqual(request('/api/router-updates/refresh', {'id': ''})[0], 403)
            reboot_payload = {'ssh_password': '', 'request_id': 'request-api-reboot'}
            self.assertEqual(request('/api/router-updates/reboot', reboot_payload, cookie='')[0], 401)
            self.assertEqual(request('/api/router-updates/reboot', reboot_payload)[0], 403)
            with mock.patch.object(hub.Handler, 'current_account_profile', return_value={'is_owner': False}):
                self.assertEqual(request('/api/router-updates/reboot', reboot_payload, csrf=self.manager.csrf)[0], 403)
            self.assertEqual(request('/api/router-updates/reboot', reboot_payload, csrf=self.manager.csrf, origin='https://attacker.example')[0], 403)
            status, body = request('/api/router-updates')
            self.assertEqual(status, 200)
            self.assertIn('online', json.loads(body)['routers'][0])
            with self.app.conn() as conn:
                conn.execute('update routers set last_seen=?, status_json=? where id=?',
                             (hub.now_ts(), json.dumps({'name':'АНТОН ВПН', 'xray':'running'}), 'main'))
            self.assertEqual(request('/api/router-updates/start', payload, csrf=self.manager.csrf, origin='https://attacker.example')[0], 403)
            def ssh(row, script, password):
                if 'OWRT_UPDATE_READY=1' in script: return 'OWRT_UPDATE_READY=1\n'
                return 'OWRT_UPDATE_STATE=done\nOWRT_UPDATE_RC=0\n'
            with mock.patch.object(self.manager, '_ssh', side_effect=ssh):
                self.assertEqual(request('/api/router-updates/start', payload, csrf=self.manager.csrf)[0], 200)
                self.assertTrue(all(item['state'] == 'success' for item in self.finish()['items']))
            _, body = request('/api/router-updates')
            self.assertNotIn('ssh_password', body)
            data = json.loads(body)
            self.assertTrue(data['routers'][0]['online'])
            self.assertEqual(data['routers'][0]['xray'], 'running')
            self.assertIsNotNone(data['job'])
            with self.app.conn() as conn:
                conn.execute('update routers set last_seen=? where id=?', (hub.now_ts()-10000, 'main'))
            status, body = request('/api/router-updates/refresh', {'id': data['job']['id']}, csrf=self.manager.csrf)
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertIsNone(data['job'])
            self.assertFalse(data['routers'][0]['online'])
            self.assertEqual(data['routers'][0]['name'], 'АНТОН ВПН')
            def reboot_ssh(row, script, password):
                if 'OWRT_UPDATE_READY=1' in script:
                    return 'OWRT_UPDATE_READY=1\nOWRT_UPDATE_BOOT_ID=aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa\n'
                if 'OWRT_REBOOT_RUNNER' in script:
                    return 'OWRT_UPDATE_STARTED=1\n'
                return 'OWRT_UPDATE_BOOT_ID=bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb\n'
            with mock.patch.object(self.manager, '_ssh', side_effect=reboot_ssh):
                status, body = request('/api/router-updates/reboot', reboot_payload, csrf=self.manager.csrf)
                self.assertEqual(status, 200)
                self.assertTrue(all(item['state'] == 'success' for item in self.finish()['items']))
            self.assertEqual(json.loads(request('/api/router-updates')[1])['job']['operation'], 'reboot')
        finally:
            server.shutdown(); server.server_close(); thread.join(3)


class RouterTaskTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'fixture uses bundled Windows POSIX shell')
    def test_detached_reboot_receipt_single_execution_and_busybox_syntax(self):
        # Fake reboot only: this test never reboots any host or contacts routers.
        shell = r'C:\Program Files\Git\usr\bin\sh.exe'
        busybox = ROOT / '.test-output/busybox/busybox64.exe'
        with tempfile.TemporaryDirectory(prefix='owrt-reboot-shell-') as folder:
            root = Path(folder)
            task = root / 'task'
            env = {**os.environ, 'PATH': folder + os.pathsep + str(Path(shell).parent) + os.pathsep + os.environ['PATH']}
            root.joinpath('setsid').write_text('#!/usr/bin/perl\nuse POSIX (); POSIX::setsid() >= 0 or die; exec @ARGV; die;\n', encoding='ascii', newline='\n')
            root.joinpath('reboot').write_text('#!/bin/sh\necho run >>"' + root.as_posix() + '/runs"\necho fake-reboot-error\nexit 7\n', encoding='ascii', newline='\n')
            script = hub.router_reboot_launch_script('fixture', delay=0).replace('/tmp/owrt-remote-reboot-fixture', task.as_posix())
            for _ in range(2):
                result = subprocess.run([shell], input=script, capture_output=True, encoding='utf-8', env=env, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('OWRT_UPDATE_STARTED=1', result.stdout)
            deadline = time.monotonic() + 5
            while not (task / 'result').exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual((task / 'result').read_text().strip(), '7')
            self.assertEqual((task / 'output').read_text().strip(), 'fake-reboot-error')
            self.assertEqual(root.joinpath('runs').read_text().splitlines(), ['run'])
            runner = hub.router_reboot_launch_script('fixture').split("<<'OWRT_REBOOT_RUNNER'\n", 1)[1].split('\nOWRT_REBOOT_RUNNER', 1)[0]
            for source in [hub.router_reboot_probe_script(), hub.router_reboot_probe_script('fixture'), script, runner]:
                root.joinpath('syntax.sh').write_text(source, encoding='utf-8', newline='\n')
                result = subprocess.run([str(busybox), 'ash', '-n', str(root / 'syntax.sh')], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.name == 'nt', 'fixture uses bundled Windows POSIX shell')
    def test_real_detached_task_failure_pipefail_bounded_output_and_cleanup(self):
        shell = r'C:\Program Files\Git\usr\bin\sh.exe'
        with tempfile.TemporaryDirectory(prefix='owrt-update-shell-') as folder:
            root = Path(folder).as_posix()
            task_dir = root + '/task'
            env = {**os.environ, 'PATH': str(Path(shell).parent) + os.pathsep + os.environ['PATH']}
            # MSYS lacks a setsid command, but its Perl exposes the actual POSIX
            # syscall. Keep separate process groups, including real cancellation.
            setsid = Path(folder) / 'setsid'
            setsid.write_text('#!/usr/bin/perl\nuse POSIX (); POSIX::setsid() >= 0 or die "setsid: $!"; exec @ARGV; die "exec: $!";\n', encoding='utf-8', newline='\n')
            env['PATH'] = folder + os.pathsep + env['PATH']
            # Match minimal OpenWrt builds: invoking base64 must fail, even if
            # it is installed on the test host. Update transport needs none.
            Path(folder, 'base64').write_text('#!/bin/sh\necho "base64: applet not found" >&2\nexit 127\n', encoding='ascii', newline='\n')
            Path(folder, 'uci').write_text("#!/bin/sh\nprintf '%s\\n' 'АНТОН ВПН'\n", encoding='utf-8', newline='\n')
            def run(script):
                file = Path(folder) / 'invoke.sh'
                file.write_text(script.replace('/tmp/owrt-remote-update-fixture', task_dir), encoding='utf-8', newline='\n')
                return subprocess.run([shell, file.as_posix()], capture_output=True, text=True,
                                      encoding='utf-8', errors='replace', env=env, timeout=15)
            preflight = hub.router_update_fields(run(hub.router_update_preflight_script()).stdout)
            self.assertEqual(preflight['READY'], '1')
            self.assertEqual(preflight['NAME'], 'АНТОН ВПН')
            launch = hub.router_update_launch_script('fixture', 'i=0; while [ "$i" -lt 1200 ]; do echo "A line of output to keep in a bounded tail only"; i=$((i+1)); done; false | cat')
            result = run(launch)
            self.assertEqual(result.returncode, 0, result.stderr)
            deadline = time.monotonic() + 12
            while not (Path(task_dir)/'result').exists() and time.monotonic() < deadline:
                time.sleep(.05)
            polled = run(hub.router_update_poll_script('fixture'))
            fields = hub.router_update_fields(polled.stdout)
            self.assertEqual(fields['STATE'], 'done', polled.stderr + polled.stdout)
            self.assertNotEqual(fields['RC'], '0', 'wget failure must not be hidden by the succeeding shell')
            self.assertLessEqual((Path(task_dir)/'output').stat().st_size, 32768)
            self.assertLessEqual(len(fields['OUTPUT'].encode()), 8192)
            self.assertFalse((Path(task_dir)/'stream').exists())
            self.assertEqual(run(hub.router_update_poll_script('fixture', cleanup=True)).returncode, 0)
            self.assertFalse(Path(task_dir).exists())
            # A successful command yields rc=0 only after completion, and a
            # replay of the same task cannot execute that command twice.
            good = hub.router_update_launch_script('fixture', 'echo run >>"' + root + '/runs"; printf \'%s\\n\' \'АНТОН ВПН\'')
            adapted = good
            self.assertEqual(run(adapted).returncode, 0)
            self.assertEqual(run(adapted).returncode, 0)
            deadline = time.monotonic() + 5
            while not (Path(task_dir)/'result').exists() and time.monotonic() < deadline:
                time.sleep(.05)
            fields = hub.router_update_fields(run(hub.router_update_poll_script('fixture')).stdout)
            self.assertEqual(fields['RC'], '0')
            self.assertIn('АНТОН ВПН', fields['OUTPUT'])
            self.assertEqual((Path(folder)/'runs').read_text().splitlines(), ['run'])
            self.assertEqual(run(hub.router_update_poll_script('fixture', cleanup=True)).returncode, 0)
            # A short line must be visible while the installer is still alive.
            def wait_for(check, timeout=12):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if check(): return
                    time.sleep(.05)
                self.fail('Detached task did not reach expected state')
            task = Path(task_dir)
            run(hub.router_update_launch_script('fixture', 'echo small-progress; sleep 20; echo must-not-run >>"' + root + '/after-cancel"'))
            wait_for(lambda: (task/'output').exists() and 'small-progress' in (task/'output').read_text())
            self.assertFalse((task/'result').exists())
            self.assertIn('OWRT_UPDATE_CANCEL=1', run(hub.router_update_cancel_script('fixture')).stdout)
            wait_for(lambda: (task/'result').exists())
            fields = hub.router_update_fields(run(hub.router_update_poll_script('fixture')).stdout)
            self.assertEqual(fields['RC'], '130')
            self.assertEqual(fields['REASON'], 'cancelled')
            self.assertFalse((Path(folder)/'after-cancel').exists())
            run(hub.router_update_poll_script('fixture', cleanup=True))
            run(hub.router_update_launch_script('fixture', 'echo must-not-run'))
            self.assertFalse((task/'command.sh').exists(), 'A delayed launch after stop must remain blocked')
            (task/'cancel').unlink()
            task.rmdir()
            # A lost VPS connection cannot disable the router's own watchdog.
            run(hub.router_update_launch_script('fixture', 'sleep 20', timeout=1))
            wait_for(lambda: (task/'result').exists())
            fields = hub.router_update_fields(run(hub.router_update_poll_script('fixture')).stdout)
            self.assertEqual(fields['RC'], '124')
            self.assertEqual(fields['REASON'], 'timeout')
            run(hub.router_update_poll_script('fixture', cleanup=True))
            # A daemon inheriting stdout must not hold result publication open.
            command = 'sleep 12 & echo $! >"' + root + '/inherited-pid"; echo installed'
            run(hub.router_update_launch_script('fixture', command))
            wait_for(lambda: (task/'result').exists(), timeout=8)
            fields = hub.router_update_fields(run(hub.router_update_poll_script('fixture')).stdout)
            self.assertEqual(fields['RC'], '0')
            run('kill "$(cat "' + root + '/inherited-pid")" 2>/dev/null || true\n')
            run(hub.router_update_poll_script('fixture', cleanup=True))
            # A cancellation arriving before launch cannot start an installer.
            run(hub.router_update_cancel_script('fixture'))
            run(hub.router_update_launch_script('fixture', 'echo must-not-run'))
            self.assertFalse((task/'command.sh').exists())
            self.assertFalse((task/'pid').exists())
            self.assertEqual(hub.router_update_fields(run(hub.router_update_poll_script('fixture')).stdout)['RC'], '130')
            run(hub.router_update_poll_script('fixture', cleanup=True))
            (task/'cancel').unlink()
            task.rmdir()
            # All generated shell scripts must also parse under real BusyBox ash.
            busybox = ROOT / '.test-output/busybox/busybox64.exe'
            script = Path(folder)/'syntax.sh'
            # Execute the generated command decoder under BusyBox ash itself,
            # including UTF-8 bytes, without requiring any base64 applet.
            decode = next(line for line in good.splitlines() if "printf '%b'" in line)
            script.write_text(decode.split(' >"$job/command.sh"', 1)[0], encoding='ascii', newline='\n')
            decoded = subprocess.run([str(busybox), 'ash', str(script)], capture_output=True)
            self.assertEqual(decoded.returncode, 0, decoded.stderr)
            self.assertEqual(decoded.stdout, ('echo run >>"' + root + '/runs"; printf \'%s\\n\' \'АНТОН ВПН\'').encode('utf-8'))
            script.write_text(launch, encoding='utf-8', newline='\n')
            self.assertEqual(subprocess.run([str(busybox), 'ash', '-n', str(script)], capture_output=True).returncode, 0)
            script.write_text(hub.router_update_cancel_script('fixture'), encoding='utf-8', newline='\n')
            self.assertEqual(subprocess.run([str(busybox), 'ash', '-n', str(script)], capture_output=True).returncode, 0)
            runner = launch.split("<<'OWRT_UPDATE_RUNNER'\n", 1)[1].split('\nOWRT_UPDATE_RUNNER', 1)[0]
            script.write_text(runner, encoding='utf-8', newline='\n')
            self.assertEqual(subprocess.run([str(busybox), 'ash', '-n', str(script)], capture_output=True).returncode, 0)
            script.write_text(hub.router_update_preflight_script(), encoding='utf-8', newline='\n')
            self.assertEqual(subprocess.run([str(busybox), 'ash', '-n', str(script)], capture_output=True).returncode, 0)


if __name__ == '__main__': unittest.main()
