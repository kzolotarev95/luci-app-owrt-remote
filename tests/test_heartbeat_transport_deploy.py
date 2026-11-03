"""Run installation and rollback in a sandbox, without SSH or services."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from test_xray_mobile_deploy import SH, GIT_BIN, ROOT


@unittest.skipUnless(SH, "POSIX shell required")
class HeartbeatTransportDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="heartbeat-transport-deploy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.targets = {"wan.sh": self.root / "wan.sh", "agent": self.root / "agent"}
        for name, path in self.targets.items():
            path.write_text("#!/bin/sh\necho previous-" + name + "\n", encoding="utf-8", newline="\n")
        self.original = {name: path.read_bytes() for name, path in self.targets.items()}
        self.backup = self.root / "backup"
        self.env = dict(os.environ)
        if os.name == "nt":
            self.env["PATH"] = str(GIT_BIN) + os.pathsep + self.env["PATH"]
        source = (ROOT / "vps/deploy-heartbeat-transport-fix.ps1").read_text(encoding="utf-8")
        self.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.script = self.script.replace("backup_dir=/root/owrt-remote-backup-before-heartbeat-transport-v120",
                                          "backup_dir=" + shlex.quote(self.backup.as_posix()))
        self.script = self.script.replace("printf /usr/lib/owrt-remote/wan.sh", "printf %s " + shlex.quote(self.targets["wan.sh"].as_posix()))
        self.script = self.script.replace("printf /usr/sbin/owrt-remote", "printf %s " + shlex.quote(self.targets["agent"].as_posix()))

    def deploy(self, action, bad_item=None, fail_verify=False, fail_signal=False, stopped=False):
        stage = self.root / "stage"
        stage.mkdir()
        for item, source in {"wan.sh": "files/usr/lib/owrt-remote/wan.sh", "agent": "files/usr/sbin/owrt-remote"}.items():
            text = (ROOT / source).read_text(encoding="utf-8")
            if bad_item == item:
                text = "#!/bin/sh\nif true; then\n"
            stage.joinpath(item).write_text(text, encoding="utf-8", newline="\n")
        script = self.script.replace("stage=/tmp/__UPLOAD_NAME__", "stage=" + shlex.quote(stage.as_posix()))
        script = script.replace("__ACTION__", action).replace("__UPLOAD_NAME__", "sandbox")
        if fail_verify:
            script = script.replace('cmp "$source_dir/$item" "$path"', "false")
        mocks = r'''

ubus() {
    printf '%s\n' "$*" >>"$TEST_ROOT/ubus-calls"
    if [ "$3" = signal ]; then
        [ "${TEST_SIGNAL_FAIL:-0}" = 0 ] || return 1
        printf 202 >"$TEST_ROOT/pid"
    fi
    printf '{}'
}
jsonfilter() { cat >/dev/null; cat "$TEST_ROOT/pid"; }
sleep() { :; }
'''
        self.root.joinpath("pid").write_text("" if stopped else "101")
        result = subprocess.run([SH], input=mocks + script, encoding="utf-8", capture_output=True,
                                env={**self.env, "TEST_ROOT": self.root.as_posix(), "TEST_SIGNAL_FAIL": "1" if fail_signal else "0"}, timeout=30)
        self.assertFalse(stage.exists())
        self.assertFalse(list(self.root.glob("*.new-sandbox")))
        return result

    def test_install_twice_then_rollback_preserves_first_backup(self):
        for attempt in range(2):
            result = self.deploy("Install")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("HEARTBEAT_TRANSPORT_ROUTER_Install_OK", result.stdout)
            for item, path in self.targets.items():
                self.assertNotEqual(path.read_bytes(), self.original[item])
                self.assertEqual(self.backup.joinpath(item).read_bytes(), self.original[item])
        result = self.deploy("Rollback")
        self.assertEqual(result.returncode, 0, result.stderr)
        for item, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.original[item])

    def test_bad_candidate_and_missing_backup_leave_paired_files_unchanged(self):
        for item in ("agent", "wan.sh"):
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

    def test_reloads_only_heartbeat_without_touching_xray(self):
        result = self.deploy("Install")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.root.joinpath("ubus-calls").read_text()
        self.assertIn('service signal {"name":"owrt-remote","instance":"heartbeat","signal":9}', calls)
        self.assertNotIn('"instance":"xray"', calls)
        self.assertIn('Heartbeat loop reloaded', result.stdout)

    def test_stopped_loop_stays_stopped(self):
        result = self.deploy("Install", stopped=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('service signal', self.root.joinpath("ubus-calls").read_text())
        self.assertIn('Heartbeat loop is stopped', result.stdout)

    def test_failed_loop_reload_restores_paired_files_and_reports_failure(self):
        result = self.deploy("Install", fail_signal=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Failed during heartbeat-reload', result.stderr)
        self.assertNotIn('HEARTBEAT_TRANSPORT_ROUTER_Install_OK', result.stdout)
        for item, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.original[item])

    def test_verify_failure_restores_paired_files(self):
        result = self.deploy("Install", fail_verify=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Restoring the agent and WAN helper", result.stderr)
        for item, path in self.targets.items():
            self.assertEqual(path.read_bytes(), self.original[item])


if __name__ == "__main__":
    unittest.main()
