"""Compatibility deploy prepares the core without restarting or rewriting JSON."""
from test_xray_validation_deploy import XrayValidationDeploymentTests as BaseDeployment


class XrayCompatibilityDeploymentTests(BaseDeployment):
    deploy_name = 'deploy-xray-compat-fix.ps1'
    backup_name = 'owrt-remote-backup-before-xray-compat-v121'
    success_marker = 'XRAY_COMPAT_ROUTER_Install_OK'

    def setUp(self):
        super().setUp()
        self.script = self.script.replace('/usr/sbin/owrt-remote prepare-xray', 'test_prepare_xray')
        self.script = '''test_prepare_xray() { return "${PREPARE_RC:-0}"; }
''' + self.script

    def test_preparation_failure_restores_both_code_files(self):
        self.env['PREPARE_RC'] = '23'
        result = self.deploy('Install')
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertIn('prepare-compatible-xray', result.stderr)
        self.assertNotIn(self.success_marker, result.stdout)
        for item, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.original[item])

    def test_prepare_runs_only_on_install(self):
        # Running restored old code on rollback would make an unbounded network
        # request or revert the compatible binary. Rollback returns only code.
        result = self.deploy('Install')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.env['PREPARE_RC'] = '99'
        result = self.deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    import unittest
    unittest.main()
