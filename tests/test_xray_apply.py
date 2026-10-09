"""Exercise the real router CGI POST with LF/CRLF and a strict POSIX shell."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
STATE = tempfile.TemporaryDirectory(prefix="owrt-xray-apply-")
spec = importlib.util.spec_from_file_location("xray_apply_hub", ROOT / "vps/owrt-remote-hub.py")
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {"OWRT_REMOTE_STATE_DIR": STATE.name}):
    spec.loader.exec_module(hub)

SH = shutil.which("sh")
STRICT_SH = shutil.which("dash") or SH
if os.name == "nt":
    git_bin = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin"
    SH = str(git_bin / "sh.exe") if (git_bin / "sh.exe").exists() else SH
    STRICT_SH = str(git_bin / "dash.exe") if (git_bin / "dash.exe").exists() else STRICT_SH
else:
    git_bin = None


@unittest.skipUnless(SH and STRICT_SH, "POSIX sh and a strict parser are required")
class XrayApplyPostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=STATE.name)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.directory.joinpath("key").write_text("test-web-key", encoding="utf-8")
        tools = self.directory / "bin"
        tools.mkdir()
        tools.joinpath("owrt-remote").write_text(
            '#!/bin/sh\n[ "$1" != render-client ] || [ "${RENDER_FAIL:-0}" = 0 ] || exit 7\n'
            'printf "%s\\n" "$*" >>"$TEST_ROOT/operations"\n', encoding="utf-8", newline="\n")
        tools.joinpath("owrt-remote").chmod(0o755)
        self.env = {**os.environ, "TMPDIR": self.directory.as_posix(),
                    "TEST_ROOT": self.directory.as_posix(), "KEY_FILE": (self.directory / "key").as_posix(),
                    "TEST_STRICT_SH": Path(STRICT_SH).as_posix()}
        if git_bin:
            self.env["PATH"] = str(git_bin) + os.pathsep + self.env["PATH"]
        self.env["PATH"] = str(tools) + os.pathsep + self.env["PATH"]
        cgi = (ROOT / "files/www/cgi-bin/owrt-remote").read_text(encoding="utf-8")
        mocks = r'''
sh() {
    if [ "$1" = -n ]; then
        cp "$2" "$TEST_ROOT/checked.sh"
        printf 'check\n' >>"$TEST_ROOT/calls"
    else
        cp "$2" "$TEST_ROOT/executed.sh"
        printf 'execute\n' >>"$TEST_ROOT/calls"
    fi
    "$TEST_STRICT_SH" "$@"
}
render_page() { printf 'KIND=%s\nTITLE=%s\nBODY=%s\n' "$RESULT_KIND" "$RESULT_TITLE" "$RESULT_BODY"; }
'''
        self.cgi = self.directory / "cgi"
        self.cgi.write_text(cgi.replace("\nread_request\n", mocks + "\nread_request\n", 1), encoding="utf-8", newline="\n")

    def post(self, script, key="test-web-key"):
        body = urlencode({"key": key, "action": "apply_xray_settings", "xray_settings_script": script}).encode("ascii")
        result = subprocess.run([SH, self.cgi.as_posix()], input=body, capture_output=True,
                                env={**self.env, "REQUEST_METHOD": "POST", "CONTENT_TYPE": "application/x-www-form-urlencoded",
                                     "CONTENT_LENGTH": str(len(body))}, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertFalse(list(self.directory.glob("owrt-remote-*")), "CGI temporary files must be removed")
        return result.stdout.decode("utf-8")

    def test_form_crlf_is_normalized_before_strict_shell_parsing(self):
        script = "if [ x = x ]; then\n  printf '%s' 'name + percent% & quote\"'\nfi\n"
        response = self.post(script.replace("\n", "\r\n"))
        self.assertIn("KIND=ok", response)
        self.assertIn('BODY=name + percent% & quote"', response)
        self.assertNotIn(b"\r", self.directory.joinpath("checked.sh").read_bytes())
        self.assertNotIn(b"\r", self.directory.joinpath("executed.sh").read_bytes())
        self.assertEqual(self.directory.joinpath("calls").read_text(), "check\nexecute\n")

    def test_lf_and_crlf_heredoc_keep_lines_and_blank_lines(self):
        script = 'cat >"$TEST_ROOT/applied" <<\'EOF\'\nfirst\n\nlast + % &\nEOF\n'
        for ending in ("\n", "\r\n"):
            with self.subTest(ending=repr(ending)):
                self.assertIn("KIND=ok", self.post(script.replace("\n", ending)))
                self.assertEqual(self.directory.joinpath("applied").read_bytes(), b"first\n\nlast + % &\n")

    def test_incomplete_script_is_rejected_before_any_changes(self):
        response = self.post('printf changed >"$TEST_ROOT/applied"\nif [ x = x ]; then\n')
        self.assertIn("KIND=bad", response)
        self.assertIn("rc=2", response)
        self.assertIn("Настройки не изменены", response)
        self.assertFalse(self.directory.joinpath("applied").exists())
        self.assertFalse(self.directory.joinpath("executed.sh").exists())

    def test_failed_render_stops_before_later_restart_or_heartbeat(self):
        self.env["RENDER_FAIL"] = "1"
        script = 'owrt-remote render-client\nprintf restarted >"$TEST_ROOT/restarted"\n'
        response = self.post(script)
        self.assertIn("KIND=bad", response)
        self.assertIn("rc=7", response)
        self.assertFalse(self.directory.joinpath("restarted").exists())

    def test_empty_and_unauthorized_form_do_not_execute_commands(self):
        self.assertIn("KIND=warn", self.post(" \r\n\t\r\n"))
        self.assertFalse(self.directory.joinpath("calls").exists())
        response = self.post('printf changed >"$TEST_ROOT/applied"', key="wrong-key")
        self.assertIn("Доступ запрещен", response)
        self.assertFalse(self.directory.joinpath("applied").exists())
        self.assertFalse(self.directory.joinpath("calls").exists())

    def test_full_hub_config_through_form_preserves_manual_host_and_wan(self):
        self.directory.joinpath("uci").write_text("owrtremote.main.vps_host=192.0.2.25\nowrtremote.main.wan_interface=wwan\n", encoding="utf-8")
        mocks = r'''
uci() {
    [ "$1" != -q ] || shift
    case "$1" in
        get)
            [ "$2" != owrtremote.main ] || { echo remote; return; }
            awk -F= -v key="$2" '$1 == key { sub(/^[^=]*=/, ""); print; found=1; exit } END { if(!found) exit 1 }' "$TEST_ROOT/uci" ;;
        set)
            key="${2%%=*}"; value="${2#*=}"
            awk -F= -v key="$key" '$1 != key' "$TEST_ROOT/uci" >"$TEST_ROOT/uci.new"
            printf '%s=%s\n' "$key" "$value" >>"$TEST_ROOT/uci.new"
            mv "$TEST_ROOT/uci.new" "$TEST_ROOT/uci" ;;
        commit) printf 'commit\n' >>"$TEST_ROOT/operations" ;;
    esac
}
test_service() { printf 'service %s\n' "$*" >>"$TEST_ROOT/operations"; }
'''
        row = {"id": "main", "name": "test + quote's & name", "vps_host": "hub.example", "reverse_tag": "reverse-in"}
        with mock.patch.object(hub, "agent_token", return_value="test-agent-token"):
            generated = hub.make_openwrt_config(row, "https://hub.example")
        generated = generated.replace("/etc/init.d/owrt-remote", "test_service")
        response = self.post((mocks + generated).replace("\n", "\r\n"))
        self.assertIn("KIND=ok", response)
        state = self.directory.joinpath("uci").read_bytes()
        self.assertNotIn(b"\r", state)
        self.assertIn(b"owrtremote.main.vps_host=192.0.2.25\n", state)
        self.assertIn(b"owrtremote.main.wan_interface=wwan\n", state)
        self.assertIn(b"owrtremote.main.router_name=test + quote's & name\n", state)
        self.assertEqual(self.directory.joinpath("operations").read_text(),
                         "commit\nrender-client\nservice enable\nservice restart\nheartbeat\n")


if __name__ == "__main__":
    unittest.main()
