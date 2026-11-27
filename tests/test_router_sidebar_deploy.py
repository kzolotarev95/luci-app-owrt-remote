"""Run the router UI installer against disposable files, never an actual router."""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GIT_BIN = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin'
SH = str(GIT_BIN / 'sh.exe') if os.name == 'nt' else shutil.which('sh')


@unittest.skipUnless(SH, 'POSIX shell required')
class RouterSidebarDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='router-sidebar-deploy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root / 'backup'
        self.lock = self.root / 'deployment-lock'
        self.targets = {
            'cgi': self.root / 'www/cgi-bin/owrt-remote',
            'luci': self.root / 'www/luci-static/resources/view/owrt_remote.js',
            'effects': self.root / 'www/luci-static/resources/owrt_remote_effects.js',
        }
        self.initial = {
            'cgi': b'#!/bin/sh\n# old router CGI\necho old\n',
            'luci': b'/* old LuCI */\n',
            'effects': b'/* old design */\n',
        }
        self.candidate = {
            'cgi': b'#!/bin/sh\n# routerSidebar <title>OWRT Hub v125</title>\necho new\n',
            'luci': b'/* OWRT Hub v125 */\n',
            'effects': b'const HUB_DESIGN_MODES = new Set();\n',
        }
        for name, target in self.targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.initial[name])
        self.protected = {}
        for relative in ('etc/config/owrtremote', 'etc/config/xray', 'usr/sbin/owrt-remote',
                         'etc/init.d/owrt-remote', 'etc/init.d/uhttpd', 'etc/init.d/rpcd'):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'Never change or execute this file\n')
            self.protected[target] = target.read_bytes()
        source = (ROOT / 'vps/deploy-router-sidebar-ui.ps1').read_text(encoding='utf-8')
        self.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        mappings = {
            '/www/cgi-bin/owrt-remote': self.targets['cgi'].as_posix(),
            '/www/luci-static/resources/view/owrt_remote.js': self.targets['luci'].as_posix(),
            '/www/luci-static/resources/owrt_remote_effects.js': self.targets['effects'].as_posix(),
            '/root/owrt-remote-backup-before-router-sidebar-ui-v123': self.backup.as_posix(),
            '/tmp/owrt-remote-router-sidebar-ui.lock': self.lock.as_posix(),
        }
        pattern = '|'.join(re.escape(path) for path in sorted(mappings, key=len, reverse=True))
        self.script = re.sub(pattern, lambda match: mappings[match[0]], self.script)
        self.env = dict(os.environ)
        if os.name == 'nt':
            self.env['PATH'] = str(GIT_BIN) + os.pathsep + self.env['PATH']

    def deploy(self, action='Install', candidate=None, fail_verify=False, fail_replace=False):
        stage = self.root / 'stage'
        stage.mkdir()
        if action == 'Install':
            for name, contents in (candidate or self.candidate).items():
                stage.joinpath(name).write_bytes(contents)
        script = self.script.replace('stage=/tmp/__UPLOAD_NAME__', 'stage=' + shlex.quote(stage.as_posix()))
        script = script.replace('__UPLOAD_NAME__', 'sandbox').replace('__ACTION__', action)
        if fail_verify:
            script = script.replace('step=verify-installed', 'step=verify-installed\nfalse', 1)
        if fail_replace:
            # The first working file has already been atomically replaced when
            # the second rename fails. Restoration must recover the whole set.
            mock = '''
mv() {
    case "$1:$2" in
        -f:*/owrt_remote.js.new-sandbox)
            if [ ! -e "$TEST_FAILED_ONCE" ]; then : >"$TEST_FAILED_ONCE"; return 1; fi ;;
    esac
    command mv "$@"
}
'''
            script = script.replace('step=replace', mock + '\nstep=replace', 1)
            self.env['TEST_FAILED_ONCE'] = (self.root / 'failed-once').as_posix()
        result = subprocess.run([SH], input=script, encoding='utf-8', capture_output=True,
                                env=self.env, timeout=15)
        self.assertFalse(stage.exists(), result.stderr)
        self.assertFalse(list(self.root.rglob('*.new-sandbox')), 'candidate scratch files cleaned')
        for target, content in self.protected.items():
            self.assertEqual(target.read_bytes(), content, 'non-UI files preserved: ' + str(target))
        return result

    def assert_initial(self):
        for name, data in self.initial.items():
            if data is None:
                self.assertFalse(self.targets[name].exists())
            else:
                self.assertEqual(self.targets[name].read_bytes(), data)

    def test_repeated_install_preserves_first_backup_and_rollback_restores_all_files(self):
        for _ in range(2):
            result = self.deploy()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('ROUTER_SIDEBAR_UI_V123_Install_OK', result.stdout)
            for name, content in self.candidate.items():
                self.assertEqual(self.targets[name].read_bytes(), content)
                self.assertEqual(self.backup.joinpath(name).read_bytes(), self.initial[name])
            self.assertFalse(self.lock.exists())
        result = self.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ROUTER_SIDEBAR_UI_V123_Rollback_OK', result.stdout)
        self.assert_initial()

    def test_old_installation_without_effects_is_restored_to_its_original_state(self):
        self.targets['effects'].unlink()
        self.initial['effects'] = None
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.backup.joinpath('effects.missing').exists())
        self.assertTrue(self.targets['effects'].exists())
        result = self.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_initial()

    def test_missing_or_incomplete_backup_never_changes_working_files(self):
        for state in ('missing', 'partial', 'false-complete'):
            with self.subTest(state=state):
                if state == 'partial':
                    self.backup.mkdir()
                    self.backup.joinpath('cgi').write_bytes(self.initial['cgi'])
                if state == 'false-complete':
                    self.backup.joinpath('complete').touch()
                result = self.deploy('Rollback')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('No complete backup:', result.stderr)
                self.assert_initial()

    def test_bad_candidate_rejected_before_backup_or_replacement(self):
        cases = [
            {**self.candidate, 'cgi': b'#!/bin/sh\n# v123 routerSidebar\nif true; then\n'},
            {**self.candidate, 'cgi': b'#!/bin/sh\n# v123 old layout\n'},
            {**self.candidate, 'luci': b'/* v122 */\n'},
            {**self.candidate, 'luci': b'/* OWRT Hub v123 */\n'},
            {name: data for name, data in self.candidate.items() if name != 'effects'},
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                result = self.deploy(candidate=candidate)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.backup.exists())
                self.assert_initial()

    def test_incomplete_existing_backup_is_not_overwritten_by_install(self):
        self.backup.mkdir()
        keep = self.backup / 'evidence'
        keep.write_text('incomplete recovery snapshot')
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No complete backup:', result.stderr)
        self.assertEqual(keep.read_text(), 'incomplete recovery snapshot')
        self.assert_initial()

    def test_partial_replacement_failure_restores_the_working_set(self):
        result = self.deploy(fail_replace=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Restoring UI files from before this attempt.', result.stderr)
        self.assert_initial()
        self.assertFalse(self.lock.exists())

    def test_failed_verification_restores_previous_attempt_not_first_backup(self):
        self.assertEqual(self.deploy().returncode, 0)
        second = {**self.candidate, 'cgi': self.candidate['cgi'] + b'echo second\n'}
        result = self.deploy(candidate=second, fail_verify=True)
        self.assertNotEqual(result.returncode, 0)
        for name, content in self.candidate.items():
            self.assertEqual(self.targets[name].read_bytes(), content)
            self.assertEqual(self.backup.joinpath(name).read_bytes(), self.initial[name])

    def test_concurrent_attempt_does_not_remove_the_active_lock(self):
        self.lock.mkdir()
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Another router UI deployment is active:', result.stderr)
        self.assertTrue(self.lock.exists())
        self.assertFalse(self.backup.exists())
        self.assert_initial()


if __name__ == '__main__':
    unittest.main()
