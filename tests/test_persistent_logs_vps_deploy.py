"""Validate VPS install, rollback and automatic recovery in a disposable tree."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIT_BIN = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin'
SH = str(GIT_BIN / 'sh.exe') if os.name == 'nt' else shutil.which('sh')


@unittest.skipUnless(SH, 'POSIX shell required')
class VpsLogDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vps-log-deploy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'hub.py'
        self.previous = b'print("previous Hub")\n'
        self.target.write_bytes(self.previous)
        self.backup = self.root / 'hub.py.bak-before-persistent-logs-sync-v120'
        source = (ROOT / 'vps/deploy-persistent-logs-vps.ps1').read_text(encoding='utf-8')
        self.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.script = self.script.replace('/opt/owrt-remote/owrt-remote-hub.py', self.target.as_posix())
        self.script = self.script.replace('python_bin=/opt/owrt-remote/venv/bin/python', 'python_bin=python')
        self.script = self.script.replace('[ -x "$python_bin" ] || python_bin=python3', '')
        mocks = '''
systemctl() { [ "${TEST_RESTART_FAIL:-0}" != 1 ] || [ "$1" != restart ]; }
journalctl() { echo sandbox-journal; }
curl() { [ "${TEST_HEALTH_FAIL:-0}" != 1 ] && printf '{"ok":true}'; }
sleep() { return 0; }
'''
        self.script = self.script.replace('test -s "$target"', mocks + '\ntest -s "$target"', 1)
        # Exercise the actual mount/path validation with an isolated process proc
        # fixture. Storage classification is covered independently on real paths.
        original = self.root / 'running.py'
        original.write_text('print("running")')
        proc = self.root / 'proc' / '123'
        proc.mkdir(parents=True)
        (proc / 'environ').write_bytes(('OWRT_REMOTE_STATE_DIR=' + self.root.as_posix()).encode() + b'\0')
        (proc / 'cmdline').write_bytes(b'python\0' + original.as_posix().encode() + b'\0')
        database = self.root / 'hub.db'
        database.write_bytes(b'existing archive must not change')
        self.database = database
        self.script = self.script.replace("Path('/proc/' + pid", "Path('" + (self.root / 'proc').as_posix() + "/' + pid")
        self.script = self.script.replace("storage = hub.router_log_storage(db_path)",
            "storage = hub.router_log_storage(db_path)\n" +
            "storage.update(persistent=os.environ.get('TEST_RAM_DISK') != '1', writable=True)")
        self.script = self.script.replace('systemctl() { ',
            'systemctl() { if [ "$1" = show ]; then echo 123; return 0; fi; ')
        self.env = dict(os.environ)
        if os.name == 'nt':
            self.env['PATH'] = str(GIT_BIN) + os.pathsep + self.env['PATH']

    def deploy(self, action, invalid=False, **flags):
        stage = self.root / 'stage'
        stage.mkdir()
        if action == 'Install':
            data = 'if invalid:\n' if invalid else (ROOT / 'vps/owrt-remote-hub.py').read_text(encoding='utf-8')
            stage.joinpath('hub.py').write_text(data, encoding='utf-8', newline='\n')
        script = self.script.replace('stage=/tmp/__UPLOAD_NAME__', 'stage="' + stage.as_posix() + '"')
        script = script.replace('__UPLOAD_NAME__', 'sandbox').replace('__ACTION__', action)
        result = subprocess.run([SH], input=script, encoding='utf-8', capture_output=True,
                                env={**self.env, **flags}, timeout=25)
        self.assertFalse(stage.exists())
        self.assertFalse(list(self.root.glob('*.new-sandbox')))
        self.assertEqual(self.database.read_bytes(), b'existing archive must not change')
        return result

    def test_install_and_rollback_preserve_original_backup(self):
        for _ in range(2):
            result = self.deploy('Install')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PERSISTENT_LOGS_VPS_V120_Install_OK', result.stdout)
            self.assertIn(b'def receive_router_logs(', self.target.read_bytes())
            self.assertEqual(self.backup.read_bytes(), self.previous)
        result = self.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), self.previous)

    def test_failed_health_restores_file_from_this_attempt(self):
        self.assertEqual(self.deploy('Install').returncode, 0)
        installed = self.target.read_bytes()
        result = self.deploy('Rollback', TEST_HEALTH_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Restoring the previous Hub file', result.stderr)
        self.assertEqual(self.target.read_bytes(), installed)

    def test_ram_database_preflight_does_not_install_or_modify_archive(self):
        result = self.deploy('Install', TEST_RAM_DISK='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not confirmed on writable persistent disk', result.stderr)
        self.assertEqual(self.target.read_bytes(), self.previous)
        self.assertFalse(self.backup.exists())

    def test_invalid_candidate_and_missing_backup_change_nothing(self):
        self.assertNotEqual(self.deploy('Install', invalid=True).returncode, 0)
        self.assertFalse(self.backup.exists())
        self.assertNotEqual(self.deploy('Rollback').returncode, 0)
        self.assertEqual(self.target.read_bytes(), self.previous)


if __name__ == '__main__':
    unittest.main()
