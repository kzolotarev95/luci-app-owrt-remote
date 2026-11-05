"""Install/rollback DNS functions while preserving every other WAN function."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

from test_wan_dns import ROOT, SH, BUSYBOX

# Stable legacy layout: keep testing an installation without wan_dns_metric
# after this fix becomes HEAD, and when tests run from a source archive.
LEGACY_DNS = '''wan_resolve_host() {
    local host dns answer
    host="$1"; dns="$(wan_dns_server)" || return 1
    answer="$(nslookup -timeout=3 -retry=1 "$host" "$dns" 2>/dev/null)"
    printf '%s' "$answer"
}

'''


@unittest.skipUnless(BUSYBOX or SH, "POSIX shell required")
class WanDnsDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wan-dns-deploy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "wan.sh"
        self.backup = self.root / "backup"
        self.current = (ROOT / "files/usr/lib/owrt-remote/wan.sh").read_text(encoding="utf-8")
        self.published = (self.current.split("wan_dns_metric() {", 1)[0] + LEGACY_DNS
                          + "wan_endpoint_ip() {" + self.current.split("wan_endpoint_ip() {", 1)[1])
        self.target.write_text(self.published, encoding="utf-8", newline="\n")
        source = (ROOT / "vps/deploy-wan-dns-fix.ps1").read_text(encoding="utf-8")
        self.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.script = self.script.replace("target=/usr/lib/owrt-remote/wan.sh", "target=" + shlex.quote(self.target.as_posix()))
        self.script = self.script.replace("backup_dir=/root/owrt-remote-backup-before-wan-dns-v121",
                                          "backup_dir=" + shlex.quote(self.backup.as_posix()))

    def deploy(self, action="Install", bad=False, fail_verify=False, fail_signal=False):
        stage = self.root / "stage"
        stage.mkdir()
        block = "wan_dns_metric() {" + self.current.split("wan_dns_metric() {", 1)[1].split("wan_endpoint_ip() {", 1)[0]
        if bad:
            block = "if true; then\n"
        stage.joinpath("dns.sh").write_text(block, encoding="utf-8", newline="\n")
        script = self.script.replace("stage=/tmp/__UPLOAD_NAME__", "stage=" + shlex.quote(stage.as_posix()))
        script = script.replace("__UPLOAD_NAME__", "sandbox").replace("__ACTION__", action)
        if fail_verify:
            script = script.replace('cmp "$source_file" "$target"', "false")
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
        self.root.joinpath("pid").write_text("101")
        run = self.root / "run.sh"
        run.write_text(mocks + script, encoding="utf-8", newline="\n")
        args = [BUSYBOX, "ash"] if BUSYBOX else [SH]
        env = dict(os.environ)
        if not BUSYBOX and os.name == "nt":
            env["PATH"] = str(Path(SH).parent) + os.pathsep + env["PATH"]
        result = subprocess.run(args + [run.as_posix()], capture_output=True, encoding="utf-8", timeout=20,
                                env={**env, "TEST_ROOT": self.root.as_posix(), "TEST_SIGNAL_FAIL": "1" if fail_signal else "0"})
        self.assertFalse(stage.exists())
        self.assertFalse(list(self.root.glob("*.new-sandbox")))
        return result

    def test_published_and_local_transport_versions_keep_all_non_dns_code(self):
        for before in (self.published, self.current):
            with self.subTest(version="local" if before == self.current else "published"):
                self.target.write_text(before, encoding="utf-8", newline="\n")
                result = self.deploy()
                self.assertEqual(result.returncode, 0, result.stderr)
                after = self.target.read_text(encoding="utf-8")
                prefix = before.split("wan_dns_metric() {" if "wan_dns_metric() {" in before else "wan_resolve_host() {", 1)[0]
                suffix = "wan_endpoint_ip() {" + before.split("wan_endpoint_ip() {", 1)[1]
                self.assertTrue(after.startswith(prefix))
                self.assertTrue(after.endswith(suffix))
                self.assertIn("nslookup -type=A -timeout=3 -retry=1", after)
                self.assertIn("WAN_DNS_ROUTER_Install_OK", result.stdout)
                self.assertNotIn('"instance":"xray"', self.root.joinpath("ubus-calls").read_text())

    def test_repeated_install_then_rollback_preserves_first_backup(self):
        previous = self.target.read_bytes()
        for attempt in range(2):
            result = self.deploy()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.backup.joinpath("wan.sh").read_bytes(), previous)
        result = self.deploy("Rollback")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), previous)

    def test_bad_candidate_and_missing_backup_leave_target_unchanged(self):
        before = self.target.read_bytes()
        for action, bad in (("Install", True), ("Rollback", False)):
            result = self.deploy(action, bad)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.target.read_bytes(), before)
            self.assertFalse(self.backup.exists())

    def test_unknown_target_structure_is_rejected_before_changes(self):
        self.target.write_text("#!/bin/sh\necho unknown\n", encoding="utf-8", newline="\n")
        before = self.target.read_bytes()
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertFalse(self.backup.exists())

    def test_verification_or_reload_failure_restores_previous_wan_helper(self):
        before = self.target.read_bytes()
        for kwargs in ({"fail_verify": True}, {"fail_signal": True}):
            result = self.deploy(**kwargs)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Restoring WAN helper", result.stderr)
            self.assertEqual(self.target.read_bytes(), before)
            self.assertNotIn("WAN_DNS_ROUTER_Install_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
