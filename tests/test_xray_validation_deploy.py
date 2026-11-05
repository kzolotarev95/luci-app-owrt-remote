"""Run installation and rollback in a sandbox, without SSH or services."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from test_xray_mobile_deploy import SH, GIT_BIN, ROOT


@unittest.skipUnless(SH, "POSIX shell required")
class XrayValidationDeploymentTests(unittest.TestCase):
    deploy_name = 'deploy-xray-validation-fix.ps1'
    backup_name = 'owrt-remote-backup-before-xray-validation-v121'
    success_marker = 'XRAY_VALIDATION_ROUTER_Install_OK'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xray-validation-deploy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.targets = {"cgi": self.root / "cgi", "agent": self.root / "agent"}
        for name, path in self.targets.items():
            path.write_text("#!/bin/sh\necho previous-" + name + "\n", encoding="utf-8", newline="\n")
        self.original = {name: path.read_bytes() for name, path in self.targets.items()}
        self.backup = self.root / "backup"
        self.env = dict(os.environ)
        if os.name == "nt":
            self.env["PATH"] = str(GIT_BIN) + os.pathsep + self.env["PATH"]
        source = (ROOT / 'vps' / self.deploy_name).read_text(encoding="utf-8")
        self.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.script = self.script.replace('backup_dir=/root/' + self.backup_name,
                                          "backup_dir=" + shlex.quote(self.backup.as_posix()))
        self.script = self.script.replace("printf /www/cgi-bin/owrt-remote", "printf %s " + shlex.quote(self.targets["cgi"].as_posix()))
        self.script = self.script.replace("printf /usr/sbin/owrt-remote", "printf %s " + shlex.quote(self.targets["agent"].as_posix()))

    def deploy(self, action, bad_item=None, fail_verify=False):
        stage = self.root / "stage"
        stage.mkdir()
        for item, source in {"cgi": "files/www/cgi-bin/owrt-remote", "agent": "files/usr/sbin/owrt-remote"}.items():
            text = (ROOT / source).read_text(encoding="utf-8")
            if bad_item == item:
                text = "#!/bin/sh\nif true; then\n"
            stage.joinpath(item).write_text(text, encoding="utf-8", newline="\n")
        script = self.script.replace("stage=/tmp/__UPLOAD_NAME__", "stage=" + shlex.quote(stage.as_posix()))
        script = script.replace("__ACTION__", action).replace("__UPLOAD_NAME__", "sandbox")
        if fail_verify:
            script = script.replace('cmp "$source_dir/$item" "$path"', "false")
        result = subprocess.run([SH], input=script, encoding="utf-8", capture_output=True,
                                env=self.env, timeout=30)
        self.assertFalse(stage.exists())
        self.assertFalse(list(self.root.glob("*.new-sandbox")))
        return result

    def test_install_twice_then_rollback_preserves_first_backup(self):
        for attempt in range(2):
            result = self.deploy("Install")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(self.success_marker, result.stdout)
            for item, path in self.targets.items():
                self.assertNotEqual(path.read_bytes(), self.original[item])
                self.assertEqual(self.backup.joinpath(item).read_bytes(), self.original[item])
        result = self.deploy("Rollback")
        self.assertEqual(result.returncode, 0, result.stderr)
        for item, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.original[item])

    def test_bad_candidate_and_missing_backup_leave_both_files_unchanged(self):
        for item in ("cgi", "agent"):
            result = self.deploy("Install", bad_item=item)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.backup.exists())
            for name, path in self.targets.items():
                self.assertEqual(path.read_bytes(), self.original[name])
        result = self.deploy("Rollback")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No complete backup", result.stderr)
        for name, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.original[name])

    def test_verify_failure_restores_both_files(self):
        result = self.deploy("Install", fail_verify=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Restoring the agent and CGI", result.stderr)
        for item, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.original[item])


if __name__ == "__main__":
    unittest.main()
