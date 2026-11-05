"""Installation/rollback of the Xray boot fix in an isolated filesystem."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from test_xray_mobile_deploy import SH, GIT_BIN, ROOT


class XrayBootDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xray-boot-deploy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'agent'
        self.original = b'#!/bin/sh\necho previous-agent\n'
        self.target.write_bytes(self.original)
        self.backup = self.root / 'backup'
        self.config = self.root / 'client.json'
        self.config.write_text('keep working config')
        self.cache = self.root / 'cache.gz'
        self.cache.write_bytes(b'keep working cache')
        self.env = {**os.environ, 'PATH': str(GIT_BIN) + os.pathsep + os.environ['PATH'],
                    'TEST_ROOT': self.root.as_posix()}
        source = (ROOT / 'vps/deploy-xray-boot-fix.ps1').read_text(encoding='utf-8')
        self.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.script = self.script.replace('target=/usr/sbin/owrt-remote', 'target=' + shlex.quote(self.target.as_posix()))
        self.script = self.script.replace('backup_dir=/root/owrt-remote-backup-before-xray-boot-v121',
                                          'backup_dir=' + shlex.quote(self.backup.as_posix()))

    def deploy(self, action='Install', bad=False, fail_verify=False, fail_prepare=False,
               fail_signal=False, stopped=False, xray_running=True):
        stage = self.root / 'stage'
        stage.mkdir()
        if action == 'Install':
            text = '''#!/bin/sh
# xray_cache_restore()
# xray_bootstrap_lock()
[ "${TEST_PREPARE_FAIL:-0}" = 0 ] || exit 6
echo "$1" >>"$TEST_ROOT/prepared"
'''
            if bad: text = '#!/bin/sh\nif true; then\n'
            (stage / 'agent').write_text(text, encoding='utf-8', newline='\n')
        self.root.joinpath('pid').write_text('' if stopped else '101')
        script = self.script.replace('stage=/tmp/__UPLOAD_NAME__', 'stage=' + shlex.quote(stage.as_posix()))
        script = script.replace('__UPLOAD_NAME__', 'sandbox').replace('__ACTION__', action)
        if fail_verify: script = script.replace('cmp "$source_file" "$target"', 'false')
        mocks = r'''
ubus() {
    echo "$*" >>"$TEST_ROOT/ubus"
    if [ "$3" = signal ]; then
        [ "${TEST_SIGNAL_FAIL:-0}" = 0 ] || return 1
        echo 202 >"$TEST_ROOT/pid"
    fi
    echo '{}'
}
jsonfilter() {
    cat >/dev/null
    case "$2" in
        *.heartbeat.pid) cat "$TEST_ROOT/pid" ;;
        *.heartbeat.running) [ -z "$(cat "$TEST_ROOT/pid")" ] || printf true ;;
        *.xray.running) printf '%s' "$TEST_XRAY_RUNNING" ;;
    esac
    return 0
}
uci() { echo 1; }
sleep() { :; }
'''
        result = subprocess.run([SH], input=mocks + '\n' + script, capture_output=True, encoding='utf-8',
                                env={**self.env, 'TEST_PREPARE_FAIL': str(int(fail_prepare)),
                                     'TEST_SIGNAL_FAIL': str(int(fail_signal)),
                                     'TEST_XRAY_RUNNING': str(xray_running).lower()}, timeout=30)
        self.assertFalse(stage.exists())
        self.assertFalse(list(self.root.glob('*.new-sandbox')))
        self.assertEqual(self.config.read_text(), 'keep working config')
        self.assertEqual(self.cache.read_bytes(), b'keep working cache')
        return result

    def test_install_twice_and_rollback_preserve_first_backup(self):
        for _ in range(2):
            result = self.deploy()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('XRAY_BOOT_ROUTER_Install_OK', result.stdout)
            self.assertEqual((self.backup / 'agent').read_bytes(), self.original)
        result = self.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_rejected_shell_and_missing_backup_leave_agent_untouched(self):
        for action, bad in (('Install', True), ('Rollback', False)):
            result = self.deploy(action, bad=bad)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.target.read_bytes(), self.original)
            self.assertFalse(self.backup.exists())

    def test_healthy_tunnel_is_not_restarted(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / 'ubus').read_text()
        self.assertIn('"instance":"heartbeat"', calls)
        self.assertNotIn('service add', calls)
        self.assertNotIn('"instance":"xray"', calls)

    def test_stopped_tunnel_recovery_registers_only_xray(self):
        result = self.deploy(xray_running=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / 'ubus').read_text().splitlines()
        request = next(line.split('service add ', 1)[1] for line in calls if 'service add ' in line)
        self.assertEqual(set(json.loads(request)['instances']), {'xray'})

    def test_explicitly_stopped_service_stays_stopped(self):
        result = self.deploy(stopped=True, xray_running=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / 'ubus').read_text()
        self.assertNotIn('service signal', calls)
        self.assertNotIn('service add', calls)

    def test_failed_verification_or_preparation_or_reload_restore_agent(self):
        for failure in ('fail_verify', 'fail_prepare', 'fail_signal'):
            result = self.deploy(**{failure: True})
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('XRAY_BOOT_ROUTER_Install_OK', result.stdout)
            self.assertIn('Restoring the agent', result.stderr)
            self.assertEqual(self.target.read_bytes(), self.original)


if __name__ == '__main__': unittest.main()
