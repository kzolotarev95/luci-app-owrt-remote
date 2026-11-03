"""Exercise CGI deployment/rollback with temporary files, never a real router."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIT_BIN = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin"
SH = shutil.which("sh")
if os.name == "nt" and (GIT_BIN / "sh.exe").exists():
    SH = str(GIT_BIN / "sh.exe")


@unittest.skipUnless(SH, "A POSIX shell is required")
class XrayMobileDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owrt-mobile-deploy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "cgi"
        self.target.write_text("#!/bin/sh\necho previous\n", encoding="utf-8", newline="\n")
        self.backup_dir = self.root / "backup"
        self.env = {**os.environ}
        if os.name == "nt":
            self.env["PATH"] = str(GIT_BIN) + os.pathsep + self.env["PATH"]
        source = (ROOT / "vps/deploy-xray-mobile-fix.ps1").read_text(encoding="utf-8")
        self.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.script = self.script.replace("target=/www/cgi-bin/owrt-remote", "target=" + shlex.quote(self.target.as_posix()))
        self.script = self.script.replace("backup_dir=/root/owrt-remote-backup-before-xray-mobile-v120",
                                          "backup_dir=" + shlex.quote(self.backup_dir.as_posix()))

    def deploy(self, action, content=None, fail_verify=False):
        stage = self.root / "stage"
        stage.mkdir()
        if content is not None:
            stage.joinpath("cgi").write_text(content, encoding="utf-8", newline="\n")
        script = self.script.replace("stage=/tmp/__UPLOAD_NAME__", "stage=" + shlex.quote(stage.as_posix()))
        script = script.replace("__ACTION__", action).replace("__UPLOAD_NAME__", "local-test")
        if fail_verify:
            script = script.replace('cmp "$source_file" "$target"', 'false')
        result = subprocess.run([SH], input=script, encoding="utf-8", capture_output=True, env=self.env, timeout=20)
        self.assertFalse(stage.exists(), "Staging files should be cleaned")
        self.assertFalse(list(self.root.glob("cgi.new-*")))
        return result

    def test_install_then_rollback_preserve_first_backup(self):
        previous = self.target.read_bytes()
        content = (ROOT / "files/www/cgi-bin/owrt-remote").read_text(encoding="utf-8")
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                result = self.deploy("Install", content)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("XRAY_MOBILE_ROUTER_Install_OK", result.stdout)
                self.assertEqual(self.target.read_bytes(), content.encode("utf-8"))
                self.assertEqual(self.backup_dir.joinpath("owrt-remote").read_bytes(), previous)
        result = self.deploy("Rollback")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("XRAY_MOBILE_ROUTER_Rollback_OK", result.stdout)
        self.assertEqual(self.target.read_bytes(), previous)

    def test_missing_backup_is_explained_without_changes(self):
        previous = self.target.read_bytes()
        result = self.deploy("Rollback")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No backup on this router:", result.stderr)
        self.assertIn("Nothing changed.", result.stderr)
        self.assertEqual(self.target.read_bytes(), previous)

    def test_invalid_candidate_is_rejected_before_backup_or_changes(self):
        previous = self.target.read_bytes()
        result = self.deploy("Install", "#!/bin/sh\nif true; then\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Failed during preflight", result.stderr)
        self.assertFalse(self.backup_dir.exists())
        self.assertEqual(self.target.read_bytes(), previous)

    def test_failed_verification_restores_previous_file(self):
        previous = self.target.read_bytes()
        content = (ROOT / "files/www/cgi-bin/owrt-remote").read_text(encoding="utf-8")
        result = self.deploy("Install", content, fail_verify=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Failed during verify", result.stderr)
        self.assertIn("Restoring the CGI", result.stderr)
        self.assertEqual(self.target.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
