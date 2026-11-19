"""Unpublished build fixture: real files and HTTP health across a simulated service restart."""
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'vps/owrt-remote-hub.py').read_bytes().replace(b'\r\n', b'\n')
DEPLOY = ROOT / 'vps/test-hub-self-update.ps1'


class LocalTestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hub-local-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'hub.py'
        self.target.write_bytes(SOURCE)
        self.stage = self.root / 'stage'
        self.stage.mkdir()
        (self.stage / 'hub.py').write_bytes(SOURCE)
        script = DEPLOY.read_text(encoding='ascii').split("$RemoteTool = @'\n", 1)[1].split("\n'@", 1)[0]
        self.ns = {'__name__': 'local_test_helper', '__file__': str(self.stage / 'run.py')}
        exec(compile(script, 'local-test-tool.py', 'exec'), self.ns)
        self.fixture = self.root / 'self-update-local-test'
        self.ns.update(TARGET=self.target, ROOT=self.fixture, STAGE=self.stage)
        self.modules = []
        self.server = None
        self.thread = None
        self.port = 0
        self.addCleanup(self.close_server)

    def load_hub(self):
        name = 'local_test_hub_' + str(len(self.modules))
        spec = importlib.util.spec_from_file_location(name, self.target)
        hub = importlib.util.module_from_spec(spec)
        sys.modules[name] = hub
        self.modules.append(name)
        self.addCleanup(lambda: sys.modules.pop(name, None))
        # Avoid timestamp/size based bytecode reuse for rapid same-version builds.
        with mock.patch.dict(os.environ, {'OWRT_REMOTE_STATE_DIR': str(self.root / 'state')}):
            exec(compile(self.target.read_bytes(), str(self.target), 'exec'), hub.__dict__)
        return hub

    def close_server(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
            self.server = None

    def start_server(self):
        self.close_server()
        hub = self.load_hub()
        class QuietHandler(hub.Handler):
            def log_message(self, *args): pass
        app = hub.App(self.root / 'unused.db', 'session', 'agent', '')
        self.server = hub.HubHTTPServer(('127.0.0.1', self.port), QuietHandler)
        self.server.app, self.server.is_tls = app, False
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.ns['HEALTH'] = 'http://127.0.0.1:' + str(self.port) + '/health?panel-update=1'
        app.hub_update.health_url = self.ns['HEALTH'].split('?', 1)[0]
        return hub, app

    def test_full_local_fixture_update_changes_file_health_revision_and_then_restores(self):
        production_state = self.root / 'state/panel-update'
        production_state.mkdir(parents=True)
        original_state = {'job.json': b'{"status":"succeeded","id":"previous-real-update"}',
                          'previous.py': b'previous real update backup'}
        for name, data in original_state.items():
            (production_state / name).write_bytes(data)
        with mock.patch.dict(self.ns, {'idle': lambda: None, 'restart': lambda: None, 'healthy': lambda *a: True}):
            self.ns['prepare']()
        hub, app = self.start_server()
        manager = app.hub_update
        self.assertEqual(manager.directory, self.fixture / 'job')
        manager._check()
        self.assertTrue(manager.snapshot()['available'])
        self.assertIn('Локальный тест', manager.source)
        self.assertEqual(hub.RAW_REPO_BASE, 'https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main')
        revision = manager.remote_revision
        # The real Linux flock creates this directory before the space check.
        manager.directory.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(hub, 'hub_update_lock', side_effect=lambda *a, **k: nullcontext()), \
                mock.patch.object(hub.subprocess, 'run'):
            job = manager.start(revision)
        def restart(argv, **kwargs):
            self.assertEqual(argv, ['systemctl', 'restart', 'owrt-remote'])
            self.start_server()
            return SimpleNamespace(returncode=0)
        with mock.patch.object(hub, 'hub_update_lock', side_effect=lambda *a, **k: nullcontext()), \
                mock.patch.object(hub.subprocess, 'run', side_effect=restart), mock.patch.object(hub.time, 'sleep'), \
                mock.patch.object(hub, 'hub_install_log_maintenance'), mock.patch.object(hub, 'hub_prune_log_maintenance_backups'):
            self.assertEqual(hub.run_hub_update_worker(job['id']), 0)
        self.assertEqual(self.target.read_bytes(), (self.fixture / 'candidate.py').read_bytes())
        new_manager = self.server.app.hub_update
        new_manager._check()
        data = new_manager.snapshot()
        self.assertFalse(data['available'], 'updated local candidate is current')
        self.assertFalse(data['error'])
        self.assertEqual(data['current_revision'], revision)
        self.assertEqual(data['job']['status'], 'succeeded')
        with mock.patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                mock.patch('builtins.print') as output:
            self.ns['status']()
        self.assertIn(mock.call('SELF_UPDATE_LOCAL_TEST_PASSED'), output.call_args_list)
        with mock.patch.dict(self.ns, {'idle': lambda: None, 'restart': self.start_server}):
            self.ns['restore']()
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertFalse(self.fixture.exists())
        self.assertNotIn('LOCAL SELF-UPDATE TEST:', self.target.read_text(encoding='utf-8'))
        for name, data in original_state.items():
            self.assertEqual((production_state / name).read_bytes(), data)

    def test_prepare_does_not_overwrite_an_existing_test_or_backup(self):
        with mock.patch.dict(self.ns, {'idle': lambda: None, 'restart': lambda: None, 'healthy': lambda *a: True}):
            self.ns['prepare']()
            with self.assertRaisesRegex(RuntimeError, 'already exists'):
                self.ns['prepare']()
        self.assertEqual((self.fixture / 'before.py').read_bytes(), SOURCE)

    def test_failed_baseline_health_restores_original(self):
        with mock.patch.dict(self.ns, {'idle': lambda: None, 'restart': lambda: None,
                                      'healthy': mock.Mock(side_effect=[False, True])}):
            with self.assertRaisesRegex(RuntimeError, 'did not become healthy'):
                self.ns['prepare']()
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertFalse(self.fixture.exists())

    def test_retest_replaces_old_fixture_and_preserves_original_backup(self):
        with mock.patch.dict(self.ns, {'idle': lambda: None, 'restart': lambda: None, 'healthy': lambda *a: True}):
            self.ns['retest']()
            first = self.target.read_bytes()
            self.ns['retest']()
        self.assertNotEqual(first, self.target.read_bytes())
        self.assertEqual((self.fixture / 'before.py').read_bytes(), SOURCE)

    def test_status_distinguishes_missing_test_and_wrong_running_build(self):
        with mock.patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                mock.patch.dict(self.ns, {'read_health': lambda: {'ok': True, 'revision': 'other-build'}}), \
                mock.patch('builtins.print') as output:
            self.ns['status']()
        self.assertIn(mock.call('SELF_UPDATE_LOCAL_TEST_NOT_PREPARED'), output.call_args_list)
        self.assertIn('"test_prepared": false', output.call_args_list[1].args[0])
        with mock.patch.dict(self.ns, {'idle': lambda: None, 'restart': lambda: None, 'healthy': lambda *a: True}):
            self.ns['prepare']()
        with mock.patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                mock.patch.dict(self.ns, {'read_health': lambda: {'ok': True, 'revision': 'other-build'}}), \
                mock.patch('builtins.print') as output:
            self.ns['status']()
        self.assertIn('"installed_build": "baseline"', output.call_args_list[1].args[0])
        self.assertIn('"running_installed_file": false', output.call_args_list[1].args[0])

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell staging')
    def test_powershell_prepares_utf8_and_valid_python_without_connecting(self):
        self.assertTrue(DEPLOY.read_bytes().isascii())
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(DEPLOY), '-Vps', 'unused-local-test', '-PrepareOnly'],
                                capture_output=True, text=True, timeout=20,
                                env={**os.environ, 'TEMP': str(self.root), 'TMP': str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        stage = Path(next(line.split(': ', 1)[1] for line in result.stdout.splitlines() if line.startswith('Prepared only: ')))
        self.assertTrue(stage.parent.samefile(self.root), 'staging stays in the requested temporary directory')
        compile((stage / 'run.py').read_bytes(), 'prepared-tool.py', 'exec')
        self.assertEqual((stage / 'hub.py').read_bytes(), SOURCE)


if __name__ == '__main__':
    unittest.main()
