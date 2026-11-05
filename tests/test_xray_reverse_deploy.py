"""Keep JSON and code together through install, rollback and render/restart failures."""
import unittest

import test_xray_validation_deploy as router_deploy
import test_update_status_ui_deploy as vps_deploy


class ReverseRouterDeploymentTests(router_deploy.XrayValidationDeploymentTests):
    deploy_name = 'deploy-xray-reverse-fix.ps1'
    backup_name = 'owrt-remote-backup-before-xray-reverse-v121'
    success_marker = 'XRAY_REVERSE_ROUTER_Install_OK'

    @staticmethod
    def shell_path(path):
        value = path.as_posix()
        return '/' + value[0].lower() + value[2:] if path.drive else value

    def setUp(self):
        super().setUp()
        self.config_path = self.root / 'working.json'
        self.old_config = b'{"working":true}\n'
        self.config_path.write_bytes(self.old_config)
        self.env['TEST_CONFIG'] = self.shell_path(self.config_path)
        self.env['TEST_RESTART'] = (self.root / 'restart').as_posix()
        self.script = self.script.replace('/usr/sbin/owrt-remote render-client', 'test_render')
        self.script = self.script.replace('/etc/init.d/owrt-remote restart', 'test_restart')
        mocks = '''
uci() { printf '%s\\n' "$TEST_CONFIG"; }
test_render() {
    [ "${RENDER_RC:-0}" = 0 ] || return "$RENDER_RC"
    printf '{"finalRules":[]}\\n' >"$TEST_CONFIG"
}
test_restart() {
    if [ "${RESTART_RC:-0}" != 0 ] && [ ! -f "$TEST_RESTART" ]; then
        touch "$TEST_RESTART"; return "$RESTART_RC"
    fi
    echo restart >>"$TEST_RESTART"
}
'''
        self.script = mocks + self.script

    def test_install_twice_then_rollback_preserves_first_backup(self):
        super().test_install_twice_then_rollback_preserves_first_backup()
        self.assertEqual(self.config_path.read_bytes(), self.old_config)
        self.assertEqual(self.backup.joinpath('config.json').read_bytes(), self.old_config)

    def test_failed_render_does_not_restart_or_replace_json(self):
        self.env['RENDER_RC'] = '23'
        result = self.deploy('Install')
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertIn('validate-client-config', result.stderr)
        self.assertNotIn(self.success_marker, result.stdout)
        self.assertEqual(self.config_path.read_bytes(), self.old_config)
        self.assertFalse((self.root / 'restart').exists())
        for name, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.original[name])

    def test_failed_restart_restores_original_json_and_code(self):
        self.env['RESTART_RC'] = '7'
        result = self.deploy('Install')
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn('restart-tunnel', result.stderr)
        self.assertEqual(self.config_path.read_bytes(), self.old_config)
        self.assertIn('restart', (self.root / 'restart').read_text())
        for name, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.original[name])

    def test_rollback_refuses_a_different_config_path(self):
        self.assertEqual(self.deploy('Install').returncode, 0)
        other = self.root / 'different.json'
        other.write_bytes(b'{"other":true}\n')
        self.env['TEST_CONFIG'] = self.shell_path(other)
        result = self.deploy('Rollback')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('xray_config changed', result.stderr)
        self.assertEqual(other.read_bytes(), b'{"other":true}\n')


class ReverseVpsDeploymentTests(vps_deploy.UpdateStatusDeployTests):
    deploy_file = 'deploy-xray-reverse-vps.ps1'
    backup_name = 'hub.py.bak-before-xray-reverse-v121'
    success_marker = 'XRAY_REVERSE_VPS_Install_OK'


if __name__ == '__main__':
    unittest.main()
