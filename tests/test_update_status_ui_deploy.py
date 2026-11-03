"""Do not confirm a local UI install when HTTP still serves another build."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIT_BIN = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin'
SH = str(GIT_BIN / 'sh.exe') if os.name == 'nt' else shutil.which('sh')


@unittest.skipUnless(SH, 'POSIX shell required')
class UpdateStatusDeployTests(unittest.TestCase):
    deploy_file = 'deploy-update-status-ui.ps1'
    backup_name = 'hub.py.bak-before-update-status-ui-v120'
    success_marker = 'UPDATE_STATUS_UI_V120_Install_OK'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='update-status-ui-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'hub.py'
        self.previous = b'print("previous Hub")\n'
        self.target.write_bytes(self.previous)
        self.backup = self.root / self.backup_name
        self.running_revision = None
        self.report_revision = True
        self.request_paths = []
        test = self

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                test.request_paths.append(self.path)
                data = {'ok': True}
                if test.report_revision:
                    data['revision'] = (test.running_revision or
                                        hashlib.sha256(test.target.read_bytes()).hexdigest())
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), HealthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        source = (ROOT / 'vps' / self.deploy_file).read_text(encoding='ascii')
        self.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.script = self.script.replace('/opt/owrt-remote/owrt-remote-hub.py', self.target.as_posix())
        self.script = self.script.replace('python_bin=/opt/owrt-remote/venv/bin/python',
                                          'python_bin="' + Path(sys.executable).as_posix() + '"')
        self.script = self.script.replace('[ -x "$python_bin" ] || python_bin=python3', '')
        self.script = self.script.replace('http://127.0.0.1:8088',
                                          'http://127.0.0.1:' + str(self.server.server_port))
        mocks = '''
systemctl() { return 0; }
journalctl() { echo sandbox-journal; }
sleep() { return 0; }
'''
        self.script = self.script.replace('test -s "$target"', mocks + '\ntest -s "$target"', 1)
        self.env = dict(os.environ)
        if os.name == 'nt':
            self.env['PATH'] = str(GIT_BIN) + os.pathsep + self.env['PATH']

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def deploy(self, action, candidate=None):
        stage = self.root / 'stage'
        stage.mkdir()
        if action == 'Install':
            stage.joinpath('hub.py').write_bytes(candidate if candidate is not None else
                                                 (ROOT / 'vps/owrt-remote-hub.py').read_bytes())
        script = self.script.replace('stage=/tmp/__UPLOAD_NAME__', 'stage="' + stage.as_posix() + '"')
        script = script.replace('__UPLOAD_NAME__', 'sandbox').replace('__ACTION__', action)
        result = subprocess.run([SH], input=script, encoding='utf-8', capture_output=True,
                                env=self.env, timeout=30)
        self.assertFalse(stage.exists(), 'temporary HTTP output and upload are cleaned up')
        self.assertFalse(list(self.root.glob('*.new-sandbox')))
        return result

    def test_install_and_rollback_confirm_running_revision_and_preserve_backup(self):
        for _ in range(2):
            result = self.deploy('Install')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(self.success_marker, result.stdout)
            self.assertIn(hashlib.sha256(self.target.read_bytes()).hexdigest(), result.stdout)
            self.assertEqual(self.backup.read_bytes(), self.previous)
        result = self.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), self.previous)
        self.assertTrue(all(path == '/health?panel-update=1' for path in self.request_paths))

    def test_old_running_build_is_rejected_despite_ok_health_and_file_replacement(self):
        self.running_revision = hashlib.sha256(self.previous).hexdigest()
        result = self.deploy('Install')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.success_marker, result.stdout)
        self.assertIn('Running Hub revision does not match', result.stderr)
        self.assertIn(self.running_revision, result.stderr)
        self.assertEqual(self.target.read_bytes(), self.previous)
        self.assertEqual(self.backup.read_bytes(), self.previous)

    def test_health_without_revision_cannot_confirm_install(self):
        self.report_revision = False
        result = self.deploy('Install')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('(not reported)', result.stderr)
        self.assertEqual(self.target.read_bytes(), self.previous)

    def test_invalid_candidate_never_replaces_working_file(self):
        result = self.deploy('Install', candidate=b'if invalid:\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.target.read_bytes(), self.previous)
        self.assertFalse(self.request_paths)


class VersionDeployTests(UpdateStatusDeployTests):
    deploy_file = 'deploy-version-v120.ps1'
    backup_name = 'hub.py.bak-before-v120'
    success_marker = 'HUB_V120_Install_OK'


if __name__ == '__main__':
    unittest.main()
