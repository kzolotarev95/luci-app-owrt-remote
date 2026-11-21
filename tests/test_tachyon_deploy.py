"""Exercise install/rollback and failure recovery in a disposable router tree."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

from test_host_policy import ROOT, SH


@unittest.skipUnless(SH, 'POSIX shell required')
class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tachyon-deploy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(os.environ)
        if os.name == 'nt':
            self.env['PATH'] = str(Path(SH).parent) + os.pathsep + self.env['PATH']
        self.targets = {name: self.root / name for name in ('agent', 'tachyon.sh')}
        for name in ('agent',):
            self.targets[name].write_text('#!/bin/sh\necho previous\n')
        self.before = {name: path.read_bytes() if path.exists() else None for name, path in self.targets.items()}
        self.script = (ROOT / 'vps/deploy-tachyon.ps1').read_text().split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.script = self.script.replace('backup=/root/owrt-remote-backup-before-__BACKUP_NAME__-v121', 'backup=' + shlex.quote((self.root / 'backup').as_posix()))
        for name, target in self.targets.items():
            original = {'agent': '/usr/sbin/owrt-remote', 'tachyon.sh': '/usr/lib/owrt-remote/tachyon.sh'}[name]
            self.script = self.script.replace('echo ' + original, 'echo ' + shlex.quote(target.as_posix()))
        self.script = self.script.replace('/etc/init.d/owrt-remote restart', 'test_restart')
        mocks = '''
ubus() { printf '{}'; }
jsonfilter() { printf true; }
timeout() { return 0; }
busybox() { return 127; }
test_restart() { [ "${FAIL_RESTART:-0}" != 1 ]; }
sleep() { :; }
'''
        self.script = mocks + self.script.replace('test -r /usr/lib/owrt-remote/control.sh', 'true')

    def deploy(self, action='Install', bad=False, fail=False, no_timeout=False):
        stage = self.root / 'stage'
        stage.mkdir()
        for name, source in {'agent': 'files/usr/sbin/owrt-remote', 'tachyon.sh': 'files/usr/lib/owrt-remote/tachyon.sh'}.items():
            text = 'if true; then\n' if bad and name == 'tachyon.sh' else (ROOT / source).read_text(encoding='utf-8')
            stage.joinpath(name).write_text(text, encoding='utf-8', newline='\n')
        script = self.script.replace('stage=/tmp/__UPLOAD_NAME__', 'stage=' + shlex.quote(stage.as_posix()))
        script = script.replace('__UPLOAD_NAME__', 'sandbox').replace('__KIND__', 'Router').replace('__ACTION__', action)
        if no_timeout:
            # Model the optional applet missing from both PATH and BusyBox.
            script = script.replace('command -v timeout >/dev/null 2>&1', 'false')
        result = subprocess.run([SH], input=script, text=True, encoding='utf-8', capture_output=True,
                                env={**self.env, 'FAIL_RESTART': str(int(fail))}, timeout=30)
        self.assertFalse(stage.exists())
        self.assertFalse(list(self.root.glob('*.new-sandbox')))
        return result

    def assert_original(self):
        for name, path in self.targets.items():
            self.assertEqual(path.read_bytes() if path.exists() else None, self.before[name])

    def test_install_twice_and_rollback_restore_absent_helper(self):
        for _ in range(2):
            result = self.deploy()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('TACHYON_INTEGRATION_Router_Install_OK', result.stdout)
        result = self.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_original()

    def test_invalid_candidate_and_missing_backup_do_not_change_files(self):
        self.assertNotEqual(self.deploy(bad=True).returncode, 0)
        self.assert_original()
        self.assertNotEqual(self.deploy('Rollback').returncode, 0)
        self.assert_original()

    def test_restart_failure_restores_files_from_current_attempt(self):
        result = self.deploy(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('restoring files', result.stderr)
        self.assertIn('Failed during restart', result.stderr)
        self.assertIn('Router deployment diagnostics:', result.stderr)
        self.assert_original()

    def test_missing_timeout_applet_does_not_reject_router(self):
        result = self.deploy(no_timeout=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('shell timeout fallback will be used', result.stderr)
        self.assertIn('TACHYON_INTEGRATION_Router_Install_OK', result.stdout)

    def test_failed_preflight_names_step_and_keeps_files(self):
        result = self.deploy(bad=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('tachyon.sh shell syntax check failed', result.stderr)
        self.assertIn('Failed during preflight', result.stderr)
        self.assertNotIn('restoring files', result.stderr)
        self.assert_original()


if __name__ == '__main__':
    unittest.main()
