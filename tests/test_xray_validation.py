"""Candidate validation, atomic installation and the uclient proxy regression."""
import json
import os
from pathlib import Path
import subprocess
import unittest

import test_host_policy as shell_tests

ROOT, SH = shell_tests.ROOT, shell_tests.SH
ADMIN_UUID = "11111111-1111-4111-8111-111111111111"
SSH_UUID = "22222222-2222-4222-8222-222222222222"

# BusyBox expand() without FEATURE_TR_CLASSES treats class notation as
# literal characters, unlike the GNU tr available on the test host.
CLASSLESS_TR = r'''
tr() {
    if [ "$1" = -d ]; then
        case "$2" in
            '[:space:][:cntrl:]') command tr -d '[]:spacecntrl'; return ;;
            '[:space:]') command tr -d '[]:space'; return ;;
        esac
    fi
    command tr "$@"
}
'''


@unittest.skipUnless(SH, "POSIX shell required")
class XrayValidationTests(unittest.TestCase):
    setUp = shell_tests.RouterShellTests.setUp
    config = shell_tests.RouterShellTests.config
    run_shell = shell_tests.RouterShellTests.run_shell

    def prepare(self, **overrides):
        self.target = self.directory / "current.json"
        self.target.write_text('{"working":true}\n', encoding="utf-8")
        binary = self.directory / "xray"
        binary.write_text(r'''#!/bin/sh
if [ "$1" = version ]; then printf 'Xray 26.6.27 (validation fixture)\n'; exit 0; fi
[ "$1" = -test ] && [ "$2" = -config ] || exit 99
printf '%s\n' "$*" >>"$TEST_ROOT/xray-tests"
case "$3" in *.json) ;; *) echo 'Failed to get format' >&2; exit 1 ;; esac
cmp "$TEST_ROOT/current.json" "$TEST_ROOT/original.json" || exit 98
cp "$3" "$TEST_ROOT/tested.json"
if [ "${XRAY_FAIL:-0}" != 0 ]; then
    printf '%s\n' 'Failed to start: VLESS users: "flow" does not support this value' >&2
    exit "$XRAY_FAIL"
fi
printf 'Configuration OK.\n'
''', encoding="utf-8", newline="\n")
        binary.chmod(0o755)
        self.directory.joinpath("original.json").write_bytes(self.target.read_bytes())
        values = dict(vps_host="hub.example", vps_port="8443", vless_uuid=ADMIN_UUID,
                      ssh_vless_uuid=SSH_UUID, reverse_tag="main:admin", ssh_reverse_tag="main:ssh",
                      xray_bin=binary.as_posix(), xray_config=self.target.as_posix())
        values.update(overrides)
        self.config(**values)

    def assert_no_candidate(self):
        self.assertFalse(list(self.directory.glob(".owrt-remote-client.*")))

    def test_real_xray_error_preserves_previous_config_and_exit_code(self):
        self.prepare(vless_flow="unsupported-flow")
        result = self.run_shell("render_client_config", XRAY_FAIL="1")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn('VLESS users: "flow" does not support', result.stderr)
        self.assertIn("Рабочий JSON не заменён", result.stderr)
        self.assertEqual(self.target.read_bytes(), self.directory.joinpath("original.json").read_bytes())
        self.assert_no_candidate()

    def test_success_only_installs_exact_bytes_that_passed_xray(self):
        self.prepare(vps_port="08443", admin_port="00080", ssh_port="00022")
        result = self.run_shell("render_client_config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.target.as_posix())
        self.assertIn("Configuration OK", result.stderr)
        self.assertEqual(self.target.read_bytes(), self.directory.joinpath("tested.json").read_bytes())
        config = json.loads(self.target.read_text())
        self.assertEqual(config["outbounds"][2]["settings"]["port"], 8443)
        self.assertEqual(config["outbounds"][0]["settings"]["redirect"], "127.0.0.1:80")
        self.assertEqual(config["outbounds"][0]["settings"]["finalRules"],
                         [{"action": "allow", "network": "tcp", "port": 80}])
        self.assertEqual(config["outbounds"][1]["settings"]["finalRules"],
                         [{"action": "allow", "network": "tcp", "port": 22}])
        self.assertEqual(config["outbounds"][2]["settings"]["reverse"]["tag"], "main:admin")
        self.assert_no_candidate()
        if os.name != "nt":
            self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)

    def test_invalid_uuid_port_and_conflicting_tags_cannot_replace_config(self):
        bad_values = [
            ({"vless_uuid": "not-a-uuid"}, "vless_uuid"),
            ({"ssh_vless_uuid": "bad"}, "ssh_vless_uuid"),
            ({"ssh_vless_uuid": ADMIN_UUID}, "должны различаться"),
            ({"vps_port": "65536"}, "vps_port"),
            ({"admin_port": "abc"}, "admin_port"),
            ({"ssh_port": "0"}, "ssh_port"),
            ({"reverse_tag": "main admin"}, "reverse_tag"),
            ({"ssh_reverse_tag": "main:admin"}, "должны различаться"),
            ({"reverse_tag": "router-admin"}, "внутренней меткой"),
        ]
        for values, message in bad_values:
            with self.subTest(values=values):
                self.prepare(**values)
                result = self.run_shell("render_client_config")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.target.read_bytes(), self.directory.joinpath("original.json").read_bytes())
                self.assert_no_candidate()

    def test_multiline_uuid_and_tag_are_rejected_without_silently_rewriting(self):
        self.prepare()
        result = self.run_shell('xray_valid_uuid "$TEST_VALUE"', TEST_VALUE=ADMIN_UUID + "\nextra")
        self.assertNotEqual(result.returncode, 0)
        result = self.run_shell('xray_valid_tag reverse_tag "$TEST_VALUE"', TEST_VALUE="main\nadmin")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reverse_tag", result.stderr)
        self.assertEqual(self.target.read_bytes(), self.directory.joinpath("original.json").read_bytes())

    def test_openwrt_classless_tr_preserves_hub_tags_and_host(self):
        self.prepare(reverse_tag="reverse-in", ssh_reverse_tag="reverse-in-ssh")
        old_check = self.run_shell(CLASSLESS_TR + "printf '%s' reverse-in | tr -d '[:space:][:cntrl:]'")
        self.assertEqual(old_check.stdout, "v-i", "The old validator falsely changed a valid tag")
        result = self.run_shell(CLASSLESS_TR + "render_client_config")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(self.target.read_text())
        self.assertEqual(config["outbounds"][2]["settings"]["reverse"]["tag"], "reverse-in")
        self.assertEqual(config["outbounds"][3]["settings"]["reverse"]["tag"], "reverse-in-ssh")
        self.assertEqual(config["outbounds"][2]["settings"]["address"], "hub.example")
        self.assertEqual(self.target.read_bytes(), self.directory.joinpath("tested.json").read_bytes())
        self.assert_no_candidate()

    def test_openwrt_classless_tr_still_rejects_real_whitespace_and_controls(self):
        self.prepare()
        result = self.run_shell(CLASSLESS_TR + r'''
for octal in 001 011 012 015 037 040 177; do
    control=$(printf "\\$octal."); control=${control%.}
    if xray_valid_tag reverse_tag "reverse-in${control}"; then
        echo "Accepted invalid control $octal" >&2; exit 99
    fi
done
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        for host in ("hub example", "hub\texample", "hub\x01example", "hub\x7fexample"):
            with self.subTest(host=repr(host)):
                self.prepare(vps_host=host)
                result = self.run_shell(CLASSLESS_TR + "render_client_config")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("host содержит", result.stderr)
                self.assertEqual(self.target.read_bytes(), self.directory.joinpath("original.json").read_bytes())
                self.assert_no_candidate()

    def test_missing_xray_cannot_replace_config(self):
        self.prepare()
        result = self.run_shell('ensure_xray_available() { echo "Xray unavailable" >&2; return 1; }; render_client_config')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Xray unavailable", result.stderr)
        self.assertEqual(self.target.read_bytes(), self.directory.joinpath("original.json").read_bytes())
        self.assert_no_candidate()

    def test_stdout_download_does_not_replace_or_test_working_config(self):
        self.prepare()
        result = self.run_shell("render_client_config --stdout")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["outbounds"][3]["settings"]["id"], SSH_UUID)
        self.assertFalse(self.directory.joinpath("xray-tests").exists())
        self.assertEqual(self.target.read_bytes(), self.directory.joinpath("original.json").read_bytes())
        self.assert_no_candidate()

    def test_json_escaping_preserves_controls_quotes_and_unicode(self):
        value = 'a\\b"c\td\ne\rf\x01 Дом\n\n'
        result = self.run_shell('json_string "$TEST_VALUE"', TEST_VALUE=value)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), value)

    @unittest.skipUnless(os.environ.get("OWRT_TEST_XRAY_BIN"), "Set OWRT_TEST_XRAY_BIN to exercise real Xray")
    def test_real_xray_accepts_candidate_and_rejects_flow_without_replacing(self):
        self.prepare(xray_bin=Path(os.environ["OWRT_TEST_XRAY_BIN"]).as_posix())
        result = self.run_shell("render_client_config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Configuration OK", result.stderr)
        working = self.target.read_bytes()
        self.config(vps_host="hub.example", vps_port="8443", vless_uuid=ADMIN_UUID,
                    ssh_vless_uuid=SSH_UUID, reverse_tag="main:admin", ssh_reverse_tag="main:ssh",
                    vless_flow="not-supported", xray_bin=Path(os.environ["OWRT_TEST_XRAY_BIN"]).as_posix(),
                    xray_config=self.target.as_posix())
        result = self.run_shell("render_client_config")
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertIn('flow', result.stderr)
        self.assertIn('not-supported', result.stderr)
        self.assertNotIn('Configuration OK', result.stderr)
        self.assertEqual(self.target.read_bytes(), working)
        self.assert_no_candidate()

    @unittest.skipUnless(os.environ.get('OWRT_TEST_XRAY_BIN') and os.environ.get('OWRT_TEST_NEW_XRAY_BIN'),
                         'Set compatible and incompatible real Xray binaries')
    def test_real_generated_public_reverse_config_matches_the_reported_failure(self):
        old = Path(os.environ['OWRT_TEST_XRAY_BIN']).as_posix()
        new = Path(os.environ['OWRT_TEST_NEW_XRAY_BIN']).as_posix()
        for address in ('example.com', '8.8.8.8'):
            self.prepare(vps_host=address, xray_bin=old)
            result = self.run_shell('render_client_config')
            self.assertEqual(result.returncode, 0, result.stderr)
            working = self.target.read_bytes()
            # Deliberately skip migration once to reproduce the exact rc=23
            # with a new core against the very same generated configuration.
            self.config(vps_host=address, vless_uuid=ADMIN_UUID, ssh_vless_uuid=SSH_UUID,
                        reverse_tag='main:admin', ssh_reverse_tag='main:ssh',
                        xray_bin=new, xray_config=self.target.as_posix())
            result = self.run_shell('ensure_xray_available() { return 0; }; render_client_config')
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertIn('vless without TLS', result.stderr)
            self.assertEqual(self.target.read_bytes(), working)
            self.assert_no_candidate()

    def test_hub_client_and_server_payload_agree_on_uuids_ports_and_routes(self):
        row = dict(id="test-router", name="test", role="node", vps_host="hub.example", vless_port=8443,
                   vless_uuid=ADMIN_UUID, ssh_vless_uuid=SSH_UUID, reverse_tag="main:admin", ssh_reverse_tag="main:ssh",
                   vless_encryption="none", vless_flow="", admin_host="127.0.0.1", admin_port=80,
                   ssh_host="127.0.0.1", ssh_port=22, entry_port=18100, ssh_entry_port=18200)
        payload = shell_tests.hub.build_openwrt_config_payload(row, "https://hub.example")
        self.prepare(vps_host=payload["vps_host"], vps_port=str(payload["vps_port"]),
                     vless_uuid=payload["vless_uuid"], ssh_vless_uuid=payload["ssh_vless_uuid"],
                     reverse_tag=payload["reverse_tag"], ssh_reverse_tag=payload["ssh_reverse_tag"])
        result = self.run_shell("render_client_config --stdout")
        self.assertEqual(result.returncode, 0, result.stderr)
        client = json.loads(result.stdout)
        hub_client = shell_tests.hub.make_router_xray_config(row)
        for index in (0, 1):
            self.assertEqual(client["outbounds"][index]["settings"],
                             hub_client["outbounds"][index]["settings"])
        server = shell_tests.hub.make_server_xray_config([row], listen_port=8443)
        inbound = server["inbounds"][0]
        self.assertEqual(inbound["port"], client["outbounds"][2]["settings"]["port"])
        users = inbound["settings"]["clients"]
        self.assertEqual([x["id"] for x in users],
                         [client["outbounds"][i]["settings"]["id"] for i in (2, 3)])
        # Each end names its reverse stream locally. Client routing uses its
        # own tags; server routing uses the tags assigned to its two users.
        for i, outbound_index in enumerate((2, 3)):
            self.assertEqual(client["routing"]["rules"][i]["inboundTag"],
                             [client["outbounds"][outbound_index]["settings"]["reverse"]["tag"]])
            self.assertEqual(server["routing"]["rules"][i]["outboundTag"], users[i]["reverse"]["tag"])

    def test_uclient_empty_proxy_context_failure_is_reproduced_then_removed(self):
        self.prepare()
        self.env.update(http_proxy="", https_proxy="", HTTP_PROXY="http://unwanted.example:8080",
                        ALL_PROXY="socks5://unwanted.example:1080")
        # Mirrors uclient's getenv(pointer) check: an exported empty value is
        # still a proxy URL, and uclient_new("") fails before any request.
        mocks = r'''
command() {
    if [ "$1" = -v ]; then
        case "$2" in
            curl) return 1 ;;
            wget) [ "$TEST_FETCH_TOOL" = wget ]; return ;;
            uclient-fetch) [ "$TEST_FETCH_TOOL" = uclient-fetch ]; return ;;
        esac
    fi
    builtin command "$@"
}
wget() { fake_uclient; }
uclient-fetch() { fake_uclient; }
fake_uclient() {
    if [ "${http_proxy+x}" = x ] || [ "${https_proxy+x}" = x ]; then
        echo 'Failed to allocate uclient context' >&2
        return 1
    fi
    [ "${HTTP_PROXY+x}" != x ] && [ "${ALL_PROXY+x}" != x ] || return 97
    printf '{"ok":true}'
}
'''
        for tool in ("wget", "uclient-fetch"):
            with self.subTest(tool=tool):
                broken = self.run_shell(mocks + '\nfake_uclient', TEST_FETCH_TOOL=tool)
                self.assertEqual(broken.returncode, 1)
                self.assertIn("Failed to allocate uclient context", broken.stderr)
                result = self.run_shell(mocks + '\npost_json https://hub.example/api/heartbeat token /tmp/payload',
                                        TEST_FETCH_TOOL=tool)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"ok": True})


if __name__ == "__main__":
    unittest.main()
