"""The button's maintenance transaction, per-update rollback and bounded backups."""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('component_update_hub', ROOT / 'vps/owrt-remote-hub.py')
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {'OWRT_REMOTE_STATE_DIR': tempfile.gettempdir()}):
    spec.loader.exec_module(hub)


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.job_id = 'c' * 32
        self.result = self.directory / 'result.json'
        self.result.write_text(json.dumps({'day': datetime.date.today().isoformat(), 'ok': True}))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(hub, 'HUB_LOG_MAINTENANCE_RESULT', self.result).start()
        mock.patch.object(hub.shutil, 'which', return_value='/usr/bin/command').start()

    def test_success_verifies_cleanup_timer_and_exact_payload(self):
        with mock.patch.object(hub, 'hub_log_maintenance_command') as command:
            receipt = hub.hub_install_log_maintenance(self.directory, self.job_id)
        stage = hub.hub_log_maintenance_directory(self.directory, self.job_id)
        self.assertEqual((stage / 'log-maintenance.py').read_bytes(), hub.hub_log_maintenance_payload()[0])
        calls = [call.args[0] for call in command.call_args_list]
        self.assertEqual(calls[0][-3:], ['install', '--backup', str(stage / 'backup')])
        self.assertEqual(calls[1][-1], 'run')
        self.assertEqual(calls[-2:], [['systemctl', 'is-active', 'logrotate.timer'], ['systemctl', 'is-enabled', 'logrotate.timer']])
        self.assertEqual(receipt['status'], 'ready')
        self.assertEqual(hub.hub_log_maintenance_receipt(self.directory, self.job_id)['status'], 'ready')
        self.assertFalse(any('owrt-remote' in call for call in calls), 'component installation never stops heartbeat/Hub')

    def test_failure_restores_only_this_updates_snapshot_and_records_real_reason(self):
        calls = []
        def run(args, deadline):
            calls.append(args)
            if 'install' in args:
                backup = Path(args[-1]); backup.mkdir()
                (backup / 'manifest.json').write_text('{}')
            if args[-1] == 'run':
                raise RuntimeError('logrotate failed: disk full')
        with mock.patch.object(hub, 'hub_log_maintenance_command', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'disk full'):
                hub.hub_install_log_maintenance(self.directory, self.job_id)
        self.assertIn('rollback', calls[-1])
        self.assertIn(self.job_id, calls[-1][-1])
        receipt = hub.hub_log_maintenance_receipt(self.directory, self.job_id)
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn('disk full', receipt['error'])

    def test_failed_or_old_cleanup_result_cannot_be_reported_as_success(self):
        for result in ({'ok': False, 'error': 'UBIFS error'}, {'ok': True, 'day': '2000-01-01'}):
            self.result.write_text(json.dumps(result))
            with self.subTest(result=result), mock.patch.object(hub, 'hub_log_maintenance_command'):
                with self.assertRaisesRegex(RuntimeError, 'не подтверждено'):
                    hub.hub_install_log_maintenance(self.directory, self.job_id)

    def test_missing_logrotate_uses_bounded_package_installation(self):
        with mock.patch.object(hub.shutil, 'which', side_effect=lambda name: None if name == 'logrotate' else '/usr/bin/apt-get'), \
                mock.patch.object(hub, 'hub_log_maintenance_command') as command:
            hub.hub_install_log_maintenance(self.directory, self.job_id)
        args = command.call_args_list[0].args[0]
        self.assertEqual(args, ['apt-get', '-o', 'DPkg::Lock::Timeout=5', 'install', '-y', 'logrotate'])

    def test_command_errors_include_stderr_and_timeouts_are_bounded(self):
        with mock.patch.object(hub.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'nginx configuration invalid')) as run:
            with self.assertRaisesRegex(RuntimeError, 'nginx configuration invalid'):
                hub.hub_log_maintenance_command(['test'], hub.time.monotonic() + 5)
        self.assertLessEqual(run.call_args.kwargs['timeout'], 5)

    def test_retention_keeps_latest_two_snapshots_and_ignores_other_data(self):
        for number, char in enumerate('abc'):
            stage = hub.hub_log_maintenance_directory(self.directory, char * 32)
            stage.mkdir(); (stage / 'keep').write_text(char)
            os.utime(stage, (100 + number, 100 + number))
        other = self.directory / 'router-data'; other.mkdir()
        hub.hub_prune_log_maintenance_backups(self.directory, 'c' * 32)
        self.assertFalse(hub.hub_log_maintenance_directory(self.directory, 'a' * 32).exists())
        self.assertTrue(hub.hub_log_maintenance_directory(self.directory, 'b' * 32).exists())
        self.assertTrue(other.exists())


if __name__ == '__main__':
    unittest.main()
