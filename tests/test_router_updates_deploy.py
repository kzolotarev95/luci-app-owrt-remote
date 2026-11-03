"""Validate VPS install, rollback and automatic recovery in a disposable tree."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIT_BIN = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin'
SH = str(GIT_BIN / 'sh.exe') if os.name == 'nt' else shutil.which('sh')


@unittest.skipUnless(SH, 'POSIX shell required')
class RouterUpdatesDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vps-router-updates-deploy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'hub.py'
        self.previous = b'print("previous Hub")\n'
        self.target.write_bytes(self.previous)
        self.backup = self.root / 'hub.py.bak-before-router-reboot-all-v120'
        source = (ROOT / 'vps/deploy-router-updates.ps1').read_text(encoding='utf-8')
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
        return result

    def test_install_and_rollback_preserve_original_backup(self):
        for _ in range(2):
            result = self.deploy('Install')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('ROUTER_UPDATES_V120_Install_OK', result.stdout)
            self.assertIn(b'class RouterUpdateManager:', self.target.read_bytes())
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

    def test_invalid_candidate_and_missing_backup_change_nothing(self):
        self.assertNotEqual(self.deploy('Install', invalid=True).returncode, 0)
        self.assertFalse(self.backup.exists())
        self.assertNotEqual(self.deploy('Rollback').returncode, 0)
        self.assertEqual(self.target.read_bytes(), self.previous)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'),
                         'Windows PowerShell required')
    def test_windows_powershell_preserves_remote_validation(self):
        deploy = ROOT / 'vps/deploy-router-updates.ps1'
        # Without a BOM, Windows PowerShell 5.1 decodes source using the ANSI
        # code page. Keep the deployment source ASCII and test the actual
        # PowerShell-to-UTF-8 staging boundary, not just the extracted heredoc.
        self.assertTrue(deploy.read_bytes().isascii())
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
             '-File', str(deploy), '-Vps', 'unused-local-test', '-PrepareOnly'],
            capture_output=True, encoding='utf-8', errors='replace', timeout=25,
            env={**os.environ, 'TEMP': str(self.root), 'TMP': str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        prepared = next(line.removeprefix('Prepared only: ').strip()
                        for line in result.stdout.splitlines()
                        if line.startswith('Prepared only: '))
        stage = Path(prepared)
        self.assertEqual(stage.resolve().parent, self.root.resolve())
        self.assertTrue(stage.name.startswith('owrt-router-updates-'))
        remote = stage.joinpath('run.sh').read_bytes()
        self.assertTrue(remote.isascii())
        self.assertNotIn(b'\r', remote)
        expected_hub = (ROOT / 'vps/owrt-remote-hub.py').read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(stage.joinpath('hub.py').read_bytes(), expected_hub)
        validation = remote.decode('ascii').split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        checked = subprocess.run([sys.executable, '-', str(stage / 'hub.py')],
                                 input=validation, encoding='utf-8',
                                 capture_output=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn('Candidate validation OK', checked.stdout)


if __name__ == '__main__':
    unittest.main()
