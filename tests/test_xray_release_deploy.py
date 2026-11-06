"""Local release-selector payload install/rollback without router access."""
import unittest
from test_xray_apk_deploy import XrayApkDeploymentTests


class XrayReleaseDeploymentTests(XrayApkDeploymentTests):
    deploy_name = 'deploy-xray-release-fix.ps1'
    backup_name = 'owrt-remote-backup-before-xray-release-v121'
    success_marker = 'XRAY_RELEASE_ROUTER_Install_OK'

    def test_failed_xray_preparation_restores_agent_and_retains_backup(self):
        self.env['PREPARE_RC'] = '13'
        result = self.deploy('Install')
        self.assertEqual(result.returncode, 13, result.stderr)
        self.assertIn('prepare-xray-release', result.stderr)
        self.assertEqual(self.targets['agent'].read_bytes(), self.original['agent'])
        self.assertEqual(self.backup.joinpath('agent').read_bytes(), self.original['agent'])


if __name__ == '__main__': unittest.main()
