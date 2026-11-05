"""Panel self-update: source isolation, validation, restart recovery and owner-only API."""
from contextlib import nullcontext
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
STATE = tempfile.TemporaryDirectory(prefix='hub-update-tests-')
spec = importlib.util.spec_from_file_location('self_update_hub', ROOT / 'vps/owrt-remote-hub.py')
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {'OWRT_REMOTE_STATE_DIR': STATE.name}):
    spec.loader.exec_module(hub)
SOURCE = (ROOT / 'vps/owrt-remote-hub.py').read_bytes()
NEW = SOURCE + b'\n# update test build\n'
CURRENT_VERSION = int(hub.HUB_LOADED_VERSION[1:])


def versioned_source(version):
    return re.sub(rb'(appBannerVersion[^>]*>)v[0-9]+',
                  lambda match: match[1] + ('v' + str(version)).encode('ascii'),
                  SOURCE, count=1)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=STATE.name)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'hub.py'
        self.target.write_bytes(SOURCE)
        self.directory = self.root / 'update'
        self.directory.mkdir()
        self.manager = hub.HubUpdateManager(self.target, self.directory)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(hub, 'hub_update_lock', side_effect=lambda *a, **k: nullcontext()).start()

    def response(self, data=NEW, url=None):
        response = io.BytesIO(data)
        response.geturl = lambda: url or self.manager.source + '/vps/owrt-remote-hub.py'
        return response

    def prepare(self):
        with mock.patch.object(hub.urllib.request, 'urlopen', return_value=self.response()):
            self.manager._check()
        with mock.patch.object(hub.subprocess, 'run'):
            return self.manager.start(hashlib.sha256(NEW).hexdigest())

    def test_check_uses_own_repository_even_when_version_is_unchanged(self):
        for source in ('https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main',
                       'https://gitverse.ru/api/repos/kzolotarev95/luci-app-owrt-remote/raw/branch/main'):
            self.manager.source = source
            with mock.patch.object(hub.urllib.request, 'urlopen', return_value=self.response()) as fetch:
                self.manager._check()
            self.assertEqual(fetch.call_args.args[0].full_url, source + '/vps/owrt-remote-hub.py')
            data = self.manager.snapshot()
            self.assertTrue(data['available'])
            self.assertEqual(data['remote_version'], 'v121')
            self.assertEqual(data['remote_revision'], hashlib.sha256(NEW).hexdigest())
        self.assertEqual(self.target.read_bytes(), SOURCE)

    def test_unchanged_source_is_current_and_errors_do_not_offer_update(self):
        with mock.patch.object(hub.urllib.request, 'urlopen', return_value=self.response(SOURCE)):
            self.manager._check()
        self.assertFalse(self.manager.snapshot()['available'])
        for response in (b'<html>502 Bad Gateway</html>', b'X' * (hub.HUB_UPDATE_LIMIT + 1), SOURCE + b'\nif broken\n'):
            with mock.patch.object(hub.urllib.request, 'urlopen', return_value=self.response(response)):
                self.manager._check()
            data = self.manager.snapshot()
            self.assertTrue(data['error'])
            self.assertFalse(data['available'])
        self.assertEqual(self.target.read_bytes(), SOURCE)

    def test_older_published_version_is_not_offered_and_stale_start_is_rejected(self):
        older = versioned_source(CURRENT_VERSION - 1)
        with mock.patch.object(hub.urllib.request, 'urlopen', return_value=self.response(older)):
            self.manager._check()
        state = self.manager.snapshot()
        self.assertNotEqual(state['remote_revision'], state['current_revision'])
        self.assertFalse(state['available'])
        self.assertTrue(state['source_older'])
        self.assertFalse(state['error'])
        self.assertEqual(state['current_version'], hub.HUB_LOADED_VERSION)
        self.assertEqual(state['remote_version'], 'v' + str(CURRENT_VERSION - 1))
        with mock.patch.object(hub.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Понижение версии'):
                self.manager.start(state['remote_revision'])
            self.assertFalse(run.called)
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertFalse((self.directory / 'candidate.py').exists())
        self.assertFalse((self.directory / 'job.json').exists())

    def test_newer_version_is_offered_and_installed(self):
        newer = versioned_source(CURRENT_VERSION + 1)
        with mock.patch.object(hub.urllib.request, 'urlopen', return_value=self.response(newer)):
            self.manager._check()
        self.assertTrue(self.manager.snapshot()['available'])
        self.assertFalse(self.manager.snapshot()['source_older'])
        with mock.patch.object(hub.subprocess, 'run'):
            job = self.manager.start(self.manager.remote_revision)
        with mock.patch.object(hub.time, 'sleep'), mock.patch.object(hub.subprocess, 'run'), \
                mock.patch.object(hub, 'hub_update_health', return_value=True):
            self.assertEqual(hub.run_hub_update_worker(job['id'], self.directory, self.target), 0)
        self.assertEqual(self.target.read_bytes(), newer)
        self.assertEqual(hub.hub_update_state(self.directory)['status'], 'succeeded')

    def test_worker_rejects_a_downgrade_job_before_replacing_or_restarting(self):
        job = self.prepare()
        older = versioned_source(CURRENT_VERSION - 1)
        (self.directory / 'candidate.py').write_bytes(older)
        job.update(revision=hashlib.sha256(older).hexdigest(), version=hub.hub_update_version(older))
        hub.hub_update_state(self.directory, job)
        with mock.patch.object(hub.time, 'sleep'), mock.patch.object(hub.subprocess, 'run') as run:
            self.assertEqual(hub.run_hub_update_worker(job['id'], self.directory, self.target), 1)
            self.assertFalse(run.called)
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertFalse((self.directory / 'previous.py').exists())
        self.assertFalse((self.directory / 'candidate.py').exists())
        self.assertIn('Понижение версии', hub.hub_update_state(self.directory)['message'])

    def test_timeout_and_https_downgrade_never_modify_target(self):
        with mock.patch.object(hub.urllib.request, 'urlopen', side_effect=TimeoutError('DNS/TLS timeout')):
            self.manager._check()
        self.assertIn('timeout', self.manager.snapshot()['error'])
        with mock.patch.object(hub.urllib.request, 'urlopen', return_value=self.response(url='http://unsafe.example/hub.py')):
            self.manager._check()
        self.assertIn('HTTPS', self.manager.snapshot()['error'])
        self.assertEqual(self.target.read_bytes(), SOURCE)

    def test_checks_are_shared_and_throttled(self):
        self.manager.checked_at = hub.time.monotonic()
        with mock.patch.object(hub.threading, 'Thread') as thread:
            for _ in range(5): self.manager.check()
            self.assertFalse(thread.called)
            self.manager.checked_at -= 30
            self.manager.check(force=True)
            self.assertEqual(thread.call_count, 1)
            self.manager.check(force=True)
            self.assertEqual(thread.call_count, 1)

    def test_launch_survives_hub_restart_and_duplicate_request_is_rejected(self):
        job = self.prepare()
        manager_after_restart = hub.HubUpdateManager(self.target, self.directory)
        self.assertEqual(manager_after_restart.snapshot()['job']['id'], job['id'])
        with self.assertRaisesRegex(RuntimeError, 'уже выполняется'):
            self.manager.start(job['revision'])
        with mock.patch.object(hub.time, 'sleep'), mock.patch.object(hub.subprocess, 'run') as run, \
                mock.patch.object(hub, 'hub_update_health', return_value=True):
            self.assertEqual(hub.run_hub_update_worker(job['id'], self.directory, self.target), 0)
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertEqual((self.directory / 'previous.py').read_bytes(), SOURCE)
        self.assertEqual(manager_after_restart.snapshot()['job']['status'], 'succeeded')
        self.assertFalse((self.directory / 'candidate.py').exists())
        self.assertEqual(run.call_args.args[0], ['systemctl', 'restart', 'owrt-remote'])

    def test_failed_health_restores_previous_bytes_and_restarts(self):
        job = self.prepare()
        with mock.patch.object(hub.time, 'sleep'), mock.patch.object(hub.subprocess, 'run') as run, \
                mock.patch.object(hub, 'hub_update_health', side_effect=[False, True]):
            self.assertEqual(hub.run_hub_update_worker(job['id'], self.directory, self.target), 1)
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertEqual(run.call_count, 2)
        self.assertIn('Предыдущий файл восстановлен', hub.hub_update_state(self.directory)['message'])

    def test_failure_to_write_rollback_progress_does_not_prevent_restoration(self):
        job = self.prepare()
        state_write = hub.hub_update_state
        def write(directory, value=None):
            if value and value.get('status') == 'rollback': raise OSError('state disk full')
            return state_write(directory, value)
        with mock.patch.object(hub.time, 'sleep'), mock.patch.object(hub.subprocess, 'run'), \
                mock.patch.object(hub, 'hub_update_state', side_effect=write), \
                mock.patch.object(hub, 'hub_update_health', side_effect=[False, True]):
            self.assertEqual(hub.run_hub_update_worker(job['id'], self.directory, self.target), 1)
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertEqual(hub.hub_update_state(self.directory)['status'], 'failed')

    def test_changed_candidate_or_target_never_restarts_service(self):
        job = self.prepare()
        (self.directory / 'candidate.py').write_bytes(SOURCE)
        with mock.patch.object(hub.time, 'sleep'), mock.patch.object(hub.subprocess, 'run') as run:
            self.assertEqual(hub.run_hub_update_worker(job['id'], self.directory, self.target), 1)
            self.assertFalse(run.called)
        self.assertEqual(self.target.read_bytes(), SOURCE)
        job = self.prepare()
        self.target.write_bytes(SOURCE + b'\n# manual change\n')
        with mock.patch.object(hub.time, 'sleep'), mock.patch.object(hub.subprocess, 'run') as run:
            self.assertEqual(hub.run_hub_update_worker(job['id'], self.directory, self.target), 1)
            self.assertFalse(run.called)
        self.assertTrue(self.target.read_bytes().endswith(b'# manual change\n'))

    def test_launch_failure_and_stale_confirmation_preserve_working_file(self):
        with mock.patch.object(hub.urllib.request, 'urlopen', return_value=self.response()):
            self.manager._check()
        with self.assertRaises(ValueError): self.manager.start('wrong-revision')
        with mock.patch.object(hub.subprocess, 'run', side_effect=OSError('systemd unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'systemd'):
                self.manager.start(self.manager.remote_revision)
        self.assertEqual(self.manager.snapshot()['job']['status'], 'failed')
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertFalse((self.directory / 'candidate.py').exists())

    def test_dead_worker_is_not_reported_as_infinite_update(self):
        hub.hub_update_state(self.directory, {'id': 'dead', 'status': 'starting', 'started_at': hub.time.time() - 421})
        self.assertEqual(self.manager.snapshot()['job']['status'], 'failed')

    def test_atomic_failure_does_not_truncate_target(self):
        with mock.patch.object(hub.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): hub.hub_update_write(self.target, NEW)
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertFalse(list(self.root.glob('.hub.py-*')))


class HttpTests(unittest.TestCase):
    def test_owner_csrf_origin_validation_and_revision_health(self):
        class TestHandler(hub.Handler):
            def owner_ok(self): return self.headers.get('Cookie') == 'owner'
            def admin_ok(self): return self.headers.get('Cookie') in {'owner', 'delegated'}
            def log_message(self, *args): pass
        app = hub.App(Path(STATE.name) / 'unused.db', 'legacy', 'agent', '')
        server = hub.HubHTTPServer(('127.0.0.1', 0), TestHandler)
        server.app, server.is_tls = app, False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        def request(path, cookie='', payload=None, csrf='', source=''):
            headers = {'Cookie': cookie, 'X-Hub-Update-CSRF': csrf, 'Content-Type': 'application/json'}
            if source: headers['Origin'] = source
            req = urllib.request.Request(origin + path, headers=headers,
                                         data=json.dumps(payload).encode() if payload is not None else None)
            try: response = urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as error: response = error
            with response: return response.status, json.load(response)
        try:
            with mock.patch.object(app.hub_update, 'check'), mock.patch.object(app.hub_update, 'start') as start:
                self.assertEqual(request('/api/hub-update')[0], 401)
                self.assertEqual(request('/api/hub-update', 'delegated')[0], 403)
                status, data = request('/api/hub-update', 'owner')
                self.assertEqual(status, 200)
                csrf = data['csrf']
                self.assertEqual(request('/api/hub-update/start', 'owner', {'revision': 'abc'})[0], 403)
                self.assertEqual(request('/api/hub-update/start', 'owner', {'revision': 'abc'}, csrf, 'https://foreign.example')[0], 403)
                self.assertFalse(start.called)
                self.assertEqual(request('/api/hub-update/start', 'owner', {'revision': 'abc'}, csrf, origin)[0], 200)
                start.assert_called_once_with('abc')
            self.assertEqual(request('/health')[1], {'ok': True})
            self.assertEqual(request('/health?panel-update=1')[1]['revision'], hub.HUB_LOADED_REVISION)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
