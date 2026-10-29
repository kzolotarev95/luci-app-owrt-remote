"""Static validation for the local VPS request-load deployment script."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RequestLoadDeployTests(unittest.TestCase):
    def test_embedded_remote_script_is_valid_posix_shell(self):
        source = (ROOT / "vps" / "deploy-request-load-fix.ps1").read_text(encoding="utf-8")
        script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        script = script.replace("__UPLOAD_NAME__", "test-stage").replace("__ACTION__", "Install")
        shell = shutil.which("sh")
        if os.name == "nt":
            shell = str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/sh.exe")
        if not shell or not Path(shell).exists():
            self.skipTest("POSIX shell required")
        result = subprocess.run([shell, "-n"], input=script, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_policy_and_rollback_markers_are_present(self):
        source = (ROOT / "vps" / "deploy-request-load-fix.ps1").read_text(encoding="utf-8")
        for marker in (
            "owrt-remote-log-policy.conf",
            "owrt-remote-limits.conf",
            "MaxRetentionSec=7day",
            "rotate 7",
            "REQUEST_LOAD_FIX_V119_",
            "restore_optional",
        ):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
