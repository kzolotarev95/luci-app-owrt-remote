"""Install/rollback journal files against a sandbox, never a real router."""
import os
import re
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIT_BIN = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin'
SH = str(GIT_BIN / 'sh.exe') if os.name == 'nt' else shutil.which('sh')


@unittest.skipUnless(SH, 'POSIX shell required')
class PersistentLogsDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='persistent-log-deploy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.targets = {
            'cgi': '/www/cgi-bin/owrt-remote', 'agent': '/usr/sbin/owrt-remote',
            'init': '/etc/init.d/owrt-remote', 'logs.sh': '/usr/lib/owrt-remote/logs.sh',
            'logs-init': '/etc/init.d/owrt-remote-logs',
            'luci': '/www/luci-static/resources/view/owrt_remote.js',
        }
        self.initial = {}
        for item in ('cgi', 'agent', 'logs.sh', 'logs-init', 'luci'):
            path = self.root / self.targets[item].lstrip('/')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#!/bin/sh\necho previous-' + item + '\n', encoding='utf-8', newline='\n')
            self.initial[item] = path.read_bytes()
        self.backup = self.root / 'backup'
        source = (ROOT / 'vps/deploy-persistent-logs.ps1').read_text(encoding='utf-8')
        self.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        # Absolute paths are replaced only in this disposable test copy.
        mappings = {target: (self.root / target.lstrip('/')).as_posix() for target in self.targets.values()}
        mappings['/usr/lib/owrt-remote'] = (self.root / 'usr/lib/owrt-remote').as_posix()
        mappings['/www/luci-static/resources/view'] = (self.root / 'www/luci-static/resources/view').as_posix()
        mappings['/tmp/luci-indexcache'] = (self.root / 'luci-indexcache').as_posix()
        mappings['/tmp/luci-modulecache'] = (self.root / 'luci-modulecache').as_posix()
        mappings['/etc/init.d/rpcd'] = (self.root / 'etc/init.d/rpcd').as_posix()
        mappings['/etc/init.d/uhttpd'] = (self.root / 'etc/init.d/uhttpd').as_posix()
        mappings['/tmp/owrt-remote-logs'] = (self.root / 'journal').as_posix()
        mappings['/root/owrt-remote-backup-before-persistent-logs-v121'] = self.backup.as_posix()
        pattern = '|'.join(re.escape(target) for target in sorted(mappings, key=len, reverse=True))
        self.script = re.sub(pattern, lambda match: mappings[match.group()], self.script)
        # Existing sandbox service scripts only print markers. Mock readiness.
        mocks = '''
logread() { return 0; }
ubus() { printf '{"running": true}\\n'; }
mkfifo() { return 0; }
jsonfilter() { return 0; }
logger() { printf '%s\\n' "$3" | sh "$TEST_LOG_HELPER" append; }
'''
        self.script = self.script.replace('for item in cgi agent logs.sh logs-init;', mocks + '\nfor item in cgi agent logs.sh logs-init;', 1)
        self.env = {**os.environ, 'OWRT_REMOTE_LOG_DIR': (self.root / 'journal').as_posix(), 'OWRT_REMOTE_LOG_RUNTIME': (self.root / 'runtime').as_posix()}
        self.env['TEST_LOG_HELPER'] = (self.root / 'usr/lib/owrt-remote/logs.sh').as_posix()
        if os.name == 'nt':
            self.env['PATH'] = str(GIT_BIN) + os.pathsep + self.env['PATH']

    def deploy(self, action, bad=False, fail_verify=False):
        stage = self.root / 'stage'
        stage.mkdir()
        candidates = {
            'cgi': 'files/www/cgi-bin/owrt-remote', 'agent': 'files/usr/sbin/owrt-remote',
            'init': 'files/etc/init.d/owrt-remote', 'logs.sh': 'files/usr/lib/owrt-remote/logs.sh',
            'logs-init': 'files/etc/init.d/owrt-remote-logs',
            'luci': 'files/www/luci-static/resources/view/owrt_remote.js',
        }
        if action == 'Install':
            for item, source in candidates.items():
                text = (ROOT / source).read_text(encoding='utf-8')
                if item == 'logs.sh':
                    text = text.replace('legacy=/tmp/owrt-remote-logs', 'legacy=' + shlex.quote((self.root / 'legacy').as_posix()))
                    text = text.replace('log_clock() { cut -d. -f1 /proc/uptime; }', 'log_clock() { echo 1000; }')
                    text = text.replace('log_ram_storage() {', 'log_ram_storage() { return 0;', 1)
                    text = text.replace('log_flash_storage() {', 'log_flash_storage() { return 0;', 1)
                if item == 'logs-init':
                    text = '#!/bin/sh\necho logs-service-$1\n'
                if bad and item == 'cgi':
                    text = '#!/bin/sh\nif true; then\n'
                stage.joinpath(item).write_text(text, encoding='utf-8', newline='\n')
        script = self.script.replace('stage=/tmp/__UPLOAD_NAME__', 'stage=' + shlex.quote(stage.as_posix()))
        script = script.replace('__UPLOAD_NAME__', 'sandbox').replace('__ACTION__', action)
        if fail_verify:
            script = script.replace('cmp "$stage/$item" "$path"', 'false')
        result = subprocess.run([SH], input=script, encoding='utf-8', capture_output=True,
                                env=self.env, timeout=30)
        self.assertFalse(stage.exists())
        self.assertFalse(list(self.root.rglob('*.new-sandbox')))
        return result

    def assert_initial(self):
        for item, data in self.initial.items():
            self.assertEqual((self.root / self.targets[item].lstrip('/')).read_bytes(), data)

    def test_install_then_rollback_retains_first_backup(self):
        for _ in range(2):
            result = self.deploy('Install')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PERSISTENT_LOGS_ROUTER_Install_OK', result.stdout)
            for item in ('cgi', 'luci'):
                installed = self.root / self.targets[item].lstrip('/')
                self.assertIn('v121', installed.read_text(encoding='utf-8'))
            for item, data in self.initial.items():
                self.assertEqual(self.backup.joinpath(item).read_bytes(), data)
        result = self.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PERSISTENT_LOGS_ROUTER_Rollback_OK', result.stdout)
        self.assert_initial()

    def test_missing_backup_changes_nothing(self):
        result = self.deploy('Rollback')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No complete backup:', result.stderr)
        self.assert_initial()

    def test_invalid_candidate_rejected_before_backup(self):
        result = self.deploy('Install', bad=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.backup.exists())
        self.assert_initial()

    def test_failed_verification_restores_all_previous_files(self):
        result = self.deploy('Install', fail_verify=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Restoring files from this attempt.', result.stderr)
        self.assert_initial()

    def test_rollback_restores_existing_log_service_and_helper(self):
        # This fix is normally applied on top of v121's already installed journal.
        for item in ('logs.sh', 'logs-init'):
            target = self.root / self.targets[item].lstrip('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('#!/bin/sh\necho previous-' + item + '-$1\n', encoding='utf-8', newline='\n')
            self.initial[item] = target.read_bytes()
        result = self.deploy('Install')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        for item, data in self.initial.items():
            self.assertEqual((self.root / self.targets[item].lstrip('/')).read_bytes(), data)
        self.assertIn('previous-logs-init-start', result.stdout)


if __name__ == '__main__':
    unittest.main()
