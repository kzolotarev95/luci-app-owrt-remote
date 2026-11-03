"""Deploy only the journal helper/service, preserving data and networking."""
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

class CollectorDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='collector-deploy-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.targets = {'logs.sh': '/usr/lib/owrt-remote/logs.sh', 'logs-init': '/etc/init.d/owrt-remote-logs'}
        self.initial = {}
        for item, target in self.targets.items():
            path = self.root / target.lstrip('/')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#!/bin/sh\necho old-' + item + '-$1\n', newline='\n')
            self.initial[item] = path.read_bytes()
        self.backup = self.root / 'backup'
        self.journal = self.root / 'retained-journal'; self.journal.write_text('keep history')
        text = (ROOT / 'vps/deploy-log-collector-fix.ps1').read_text(encoding='utf-8')
        self.script = text.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        mapping = {target: (self.root / target.lstrip('/')).as_posix() for target in self.targets.values()}
        mapping['/root/owrt-remote-backup-before-log-collector-v120'] = self.backup.as_posix()
        pattern = '|'.join(re.escape(path) for path in sorted(mapping, key=len, reverse=True))
        self.script = re.sub(pattern, lambda m: mapping[m.group()], self.script)
        mocks = r'''
ubus() { printf '{"running":true}\n'; }
jsonfilter() { printf true; }
logread() { return 0; }
mkfifo() { return 0; }
logger() { printf '%s\n' "$3" >"$TEST_MARKER"; }
sleep() { return 0; }
'''
        self.script = self.script.replace('trap cleanup EXIT', mocks + '\ntrap cleanup EXIT')
        self.env = {**os.environ, 'PATH': str(GIT_BIN) + os.pathsep + os.environ['PATH'],
                    'TEST_MARKER': (self.root / 'marker').as_posix()}

    def deploy(self, action, bad=False, fail=False):
        stage = self.root / 'stage'; stage.mkdir()
        if action == 'Install':
            helper = r'''#!/bin/sh
# log_collector_progress()
# LOG_HEARTBEAT_SECONDS=300
case "$1" in
read) cat "$TEST_MARKER" ;;
status) echo persistent=1 ;;
esac
'''
            if bad: helper = '#!/bin/sh\nif true; then\n'
            (stage / 'logs.sh').write_text(helper, newline='\n')
            (stage / 'logs-init').write_text('#!/bin/sh\necho new-service-$1\n', newline='\n')
        script = self.script.replace('stage=/tmp/__UPLOAD_NAME__', 'stage=' + shlex.quote(stage.as_posix()))
        script = script.replace('__UPLOAD_NAME__', 'sandbox').replace('__ACTION__', action)
        if fail: script = script.replace('cmp "$source_dir/$item" "$path"', 'false')
        result = subprocess.run([SH], input=script, text=True, encoding='utf-8', capture_output=True, env=self.env, timeout=20)
        self.assertFalse(stage.exists())
        self.assertEqual(self.journal.read_text(), 'keep history')
        return result

    def test_install_and_rollback_keep_first_backup_and_journal(self):
        for _ in range(2):
            result = self.deploy('Install')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('LOG_COLLECTOR_ROUTER_Install_OK', result.stdout)
            for item, initial in self.initial.items(): self.assertEqual((self.backup / item).read_bytes(), initial)
        result = self.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        for item, target in self.targets.items(): self.assertEqual((self.root / target.lstrip('/')).read_bytes(), self.initial[item])

    def test_missing_backup_is_noop(self):
        result = self.deploy('Rollback')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Nothing changed', result.stderr)

    def test_invalid_shell_rejected_before_backup(self):
        result = self.deploy('Install', bad=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.backup.exists())

    def test_failed_verify_restores_previous_code(self):
        result = self.deploy('Install', fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Restoring journal code', result.stderr)
        for item, target in self.targets.items(): self.assertEqual((self.root / target.lstrip('/')).read_bytes(), self.initial[item])

if __name__ == '__main__': unittest.main()
