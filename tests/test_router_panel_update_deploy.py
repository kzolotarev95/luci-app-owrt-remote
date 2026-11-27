"""Verify the first installation/rollback of the router update module."""
from pathlib import Path
import re
import unittest

import test_router_sidebar_deploy as base
SH, ROOT = base.SH, base.ROOT


@unittest.skipUnless(SH, 'POSIX shell required')
class RouterUpdateDeployTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.RouterSidebarDeploymentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        paths = {
            'updater': 'usr/lib/owrt-remote/panel-update.sh',
            'downloader': 'usr/lib/owrt-remote/panel-download.sh',
            'updatejs': 'www/luci-static/resources/owrt_remote_update.js',
            'version': 'usr/share/owrt-remote/panel-version',
        }
        f.candidate['cgi'] += b'# routerUpdateDialog\n'
        for name, relative in paths.items():
            f.targets[name] = f.root / relative
            f.initial[name] = None
            f.candidate[name] = (ROOT / 'files' / relative).read_bytes()
        source = (ROOT / 'vps/deploy-router-panel-update.ps1').read_text(encoding='utf-8')
        f.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        mappings = {'/' + relative: f.targets[name].as_posix() for name, relative in paths.items()}
        mappings.update({
            '/www/cgi-bin/owrt-remote': f.targets['cgi'].as_posix(),
            '/www/luci-static/resources/view/owrt_remote.js': f.targets['luci'].as_posix(),
            '/www/luci-static/resources/owrt_remote_effects.js': f.targets['effects'].as_posix(),
            '/root/owrt-remote-backup-before-router-panel-update-v125': f.backup.as_posix(),
            '/tmp/owrt-remote-router-panel-update-deploy.lock': f.lock.as_posix(),
        })
        pattern = '|'.join(re.escape(path) for path in sorted(mappings, key=len, reverse=True))
        f.script = re.sub(pattern, lambda match: mappings[match[0]], f.script)

    def test_first_install_and_rollback_remove_only_new_files(self):
        f = self.fixture
        result = f.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ROUTER_PANEL_UPDATE_V125_Install_OK', result.stdout)
        for name, content in f.candidate.items():
            self.assertEqual(f.targets[name].read_bytes(), content)
        result = f.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        f.assert_initial()

    def test_bad_updater_rejected_before_changing_any_file(self):
        f = self.fixture
        result = f.deploy(candidate={**f.candidate, 'updater': b'if then\n'})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(f.backup.exists())
        f.assert_initial()

    def test_partial_install_failure_restores_all_seven_files(self):
        f = self.fixture
        result = f.deploy(fail_replace=True)
        self.assertNotEqual(result.returncode, 0)
        f.assert_initial()

    def test_verification_failure_restores_all_seven_files(self):
        f = self.fixture
        result = f.deploy(fail_verify=True)
        self.assertNotEqual(result.returncode, 0)
        f.assert_initial()


if __name__ == '__main__':
    unittest.main()
