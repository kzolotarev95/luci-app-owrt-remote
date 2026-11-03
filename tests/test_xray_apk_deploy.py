"""Local APK bootstrap update preserves code on failure and keeps the CGI untouched."""
import unittest

import test_xray_validation_deploy as base


class XrayApkDeploymentTests(base.XrayValidationDeploymentTests):
    deploy_name = 'deploy-xray-apk-fix.ps1'
    backup_name = 'owrt-remote-backup-before-xray-apk-v120'
    success_marker = 'XRAY_APK_ROUTER_Install_OK'

    def setUp(self):
        super().setUp()
        self.cgi_path = self.targets.pop('cgi')
        self.old_cgi = self.original.pop('cgi')
        self.key_file = self.root / 'web.key'
        self.script = self.script.replace('key_file=/etc/owrt-remote/web.key',
                                          'key_file=' + self.key_file.as_posix())
        self.script = self.script.replace('/usr/sbin/owrt-remote prepare-xray', 'test_prepare_xray')
        self.script = '''test_prepare_xray() { return "${PREPARE_RC:-0}"; }
''' + self.script

    def deploy(self, *args, **kwargs):
        result = super().deploy(*args, **kwargs)
        self.assertEqual(self.cgi_path.read_bytes(), self.old_cgi)
        return result

    def test_bad_candidate_and_missing_backup_leave_both_files_unchanged(self):
        result = self.deploy('Install', bad_item='agent')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.targets['agent'].read_bytes(), self.original['agent'])
        result = self.deploy('Rollback')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No complete backup', result.stderr)
        self.assertEqual(self.targets['agent'].read_bytes(), self.original['agent'])

    def test_failed_xray_preparation_restores_agent_and_retains_backup(self):
        self.env['PREPARE_RC'] = '13'
        result = self.deploy('Install')
        self.assertEqual(result.returncode, 13, result.stderr)
        self.assertNotIn(self.success_marker, result.stdout)
        self.assertIn('prepare-xray-apk', result.stderr)
        self.assertEqual(self.targets['agent'].read_bytes(), self.original['agent'])
        self.assertEqual(self.backup.joinpath('agent').read_bytes(), self.original['agent'])
        self.assertFalse(self.key_file.exists())

    def test_verify_failure_restores_both_files(self):
        result = self.deploy('Install', fail_verify=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Restoring the previous agent', result.stderr)
        self.assertEqual(self.targets['agent'].read_bytes(), self.original['agent'])
        self.assertFalse(self.key_file.exists())

    def test_interrupted_first_install_gets_a_key_without_replacing_existing_key(self):
        result = self.deploy('Install')
        self.assertEqual(result.returncode, 0, result.stderr)
        key = self.key_file.read_bytes()
        self.assertEqual(len(key), 32)
        self.assertTrue(all(chr(c) in '0123456789abcdef' for c in key))
        self.assertNotIn(key.decode(), result.stdout + result.stderr)
        custom = b'keep-the-existing-key\n'
        self.key_file.write_bytes(custom)
        result = self.deploy('Install')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.key_file.read_bytes(), custom)


if __name__ == '__main__':
    unittest.main()
