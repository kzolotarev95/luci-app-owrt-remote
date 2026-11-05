"""Execute rollback locally with fake router paths; never connect to a router."""
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
class WanStabilityRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owrt-wan-rollback-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.backup = self.root / "backup"
        self.files = {name: self.root / name for name in ("config", "agent", "wan.sh", "xray")}
        for name, path in self.files.items():
            path.write_text("current-" + name, encoding="utf-8")
        service = self.root / "service"
        service.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"$TEST_ROOT/service-calls"\n', encoding="utf-8", newline="\n")
        source = (ROOT / "vps/deploy-wan-stability-fix.ps1").read_text(encoding="utf-8")
        script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        script = script.replace('stage=/tmp/__UPLOAD_NAME__', 'stage=' + shlex.quote(self.stage.as_posix()))
        script = script.replace('backup=/root/owrt-remote-backup-before-wan-stability-v121', 'backup=' + shlex.quote(self.backup.as_posix()))
        script = script.replace('config=/etc/config/owrtremote', 'config=' + shlex.quote(self.files["config"].as_posix()))
        for name, original in (("agent", "/usr/sbin/owrt-remote"), ("wan.sh", "/usr/lib/owrt-remote/wan.sh")):
            script = script.replace('printf ' + original + ' ;;', "printf '%s' " + shlex.quote(self.files[name].as_posix()) + ' ;;')
        script = script.replace('/etc/init.d/owrt-remote', 'sh ' + shlex.quote(service.as_posix()))
        script = script.replace('__ACTION__', 'Rollback')
        script = script.replace('xray_config=$(uci -q get owrtremote.main.xray_config || echo /etc/xray/owrt-remote-client.json)',
                                'xray_config=' + shlex.quote(self.files["xray"].as_posix()))
        self.script = script
        self.env = {**os.environ, "TEST_ROOT": self.root.as_posix()}
        if os.name == "nt":
            self.env["PATH"] = str(GIT_BIN) + os.pathsep + self.env["PATH"]

    def run_rollback(self):
        return subprocess.run([SH], input=self.script, encoding="utf-8", capture_output=True,
                              env=self.env, timeout=20)

    def assert_unchanged(self):
        for name, path in self.files.items():
            self.assertEqual(path.read_text(), "current-" + name)
        self.assertFalse(self.root.joinpath("service-calls").exists())

    def test_missing_backup_explains_failure_without_stopping_service(self):
        result = self.run_rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No backup on this router:", result.stderr)
        self.assertIn("Nothing changed.", result.stderr)
        self.assert_unchanged()

    def test_incomplete_backup_is_rejected_before_restore(self):
        self.backup.mkdir()
        self.backup.joinpath("complete").touch()
        result = self.run_rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Backup is incomplete:", result.stderr)
        self.assert_unchanged()

    def test_complete_backup_restores_files_and_settings_then_restarts(self):
        self.backup.mkdir()
        for name in self.files:
            self.backup.joinpath(name).write_text("saved-" + name)
        self.backup.joinpath("xray-path").write_text(self.files["xray"].as_posix())
        self.backup.joinpath("complete").touch()
        result = self.run_rollback()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, path in self.files.items():
            self.assertEqual(path.read_text(), "saved-" + name)
        self.assertEqual(self.root.joinpath("service-calls").read_text(), "stop\nrestart\n")
        self.assertIn("WAN_STABILITY_ROUTER_Rollback_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
