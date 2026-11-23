"""A Hub-only design rollback must leave active router tunnels running."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from test_host_policy import ROOT, SH


@unittest.skipUnless(SH, 'POSIX shell required')
class SidebarRestartTests(unittest.TestCase):
    def restart(self, backup_name='sidebar-ui', action='Rollback', xray_active=True):
        source = (ROOT / 'vps/deploy-tachyon.ps1').read_text(encoding='utf-8')
        start = source.index('restart() {\n')
        end = source.index('\ncleanup() {', start)
        code = source[start:end].replace('__BACKUP_NAME__', backup_name)
        code = code.replace('/opt/owrt-remote/owrt-remote-run.sh', 'record_render')
        with tempfile.TemporaryDirectory(prefix='sidebar-restart-test-') as temp:
            log = Path(temp) / 'calls'
            prologue = '''set -eu
kind=Vps
action=ACTION
systemctl() {
    printf 'systemctl %s\\n' "$*" >> "$TEST_CALLS"
    if [ "$1" = is-active ]; then [ "$TEST_XRAY_ACTIVE" = 1 ]; fi
}
record_render() { printf 'render %s\\n' "$*" >> "$TEST_CALLS"; }
'''.replace('action=ACTION', 'action=' + shlex.quote(action))
            result = subprocess.run(
                [SH], input=prologue + code + '\nrestart\n', text=True,
                capture_output=True, encoding='utf-8', timeout=10,
                env={**os.environ, 'TEST_CALLS': log.as_posix(),
                     'TEST_XRAY_ACTIVE': str(int(xray_active))},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return log.read_text(encoding='utf-8').splitlines()

    def test_sidebar_install_and_rollback_restart_only_hub(self):
        for action in ('Install', 'Rollback'):
            with self.subTest(action=action):
                self.assertEqual(self.restart(action=action), ['systemctl restart owrt-remote'])

    def test_tachyon_route_rollback_keeps_its_existing_xray_refresh(self):
        self.assertEqual(self.restart(backup_name='tachyon-integration'), [
            'systemctl restart owrt-remote',
            'systemctl is-active --quiet owrt-remote-xray',
            'render render-xray --out /etc/xray/owrt-remote.json',
            'systemctl restart owrt-remote-xray',
        ])

    def test_tachyon_rollback_does_not_start_an_inactive_xray(self):
        self.assertEqual(self.restart(backup_name='tachyon-integration', xray_active=False), [
            'systemctl restart owrt-remote',
            'systemctl is-active --quiet owrt-remote-xray',
        ])


if __name__ == '__main__':
    unittest.main()
