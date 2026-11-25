"""Host locking and WAN isolation regressions; no router/network is required."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("OWRT_HOST_POLICY_HUB_SOURCE", ROOT / "vps/owrt-remote-hub.py"))
STATE = tempfile.TemporaryDirectory(prefix="owrt-host-policy-tests-")
spec = importlib.util.spec_from_file_location("host_policy_hub", SOURCE)
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {"OWRT_REMOTE_STATE_DIR": STATE.name}):
    spec.loader.exec_module(hub)

SH = shutil.which("sh")
if not SH and os.name == "nt":
    candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/sh.exe"
    SH = str(candidate) if candidate.exists() else None

MOCKS = r'''
uci() {
    [ "$1" != -q ] || shift
    case "$1" in
        get)
            if [ "$2" = owrtremote.main ]; then echo remote; return; fi
            awk -F= -v key="$2" '$1==key { sub(/^[^=]*=/, ""); print; found=1; exit } END { if (!found) exit 1 }' "$TEST_ROOT/uci"
            ;;
        set)
            key="${2%%=*}"; value="${2#*=}"
            awk -F= -v key="$key" '$1!=key' "$TEST_ROOT/uci" >"$TEST_ROOT/uci.new"
            printf '%s=%s\n' "$key" "$value" >>"$TEST_ROOT/uci.new"
            mv "$TEST_ROOT/uci.new" "$TEST_ROOT/uci"
            ;;
        commit) echo commit >>"$TEST_ROOT/commits" ;;
        delete) return 0 ;;
    esac
}
logger() { printf '%s\n' "$*" >>"$TEST_ROOT/log"; }
owrt-remote() {
    [ "$1" != status ] || { printf 'vps_host_mode: manual\n'; return; }
    [ "$1" != render-client ] || [ "${RENDER_FAIL:-0}" = 0 ]
}
ubus() { printf '{}'; }
jsonfilter() {
    if [ "$1" = -i ]; then
        "$TEST_PYTHON" -c 'import json, re, sys; data=json.load(open(sys.argv[1])); tag=re.search(r"tag=\"([^\"]+)\"", sys.argv[2]).group(1); print(next((o.get("settings", {}).get("address", "") for o in data.get("outbounds", []) if o.get("tag")==tag), ""))' "$2" "$4"
        return
    fi
    cat >/dev/null
    case "$2" in '@.l3_device') printf pppoe-wan ;; *) printf 1.1.1.1 ;; esac
}
ip() {
    local previous arg family action rule address mark priority table
    printf '%s\n' "$*" >>"$TEST_ROOT/ip"
    # BusyBox's table parser rejects large IDs (older builds even above 255).
    previous=''
    for arg in "$@"; do
        if [ "$previous" = table ] && [ "$arg" -eq "$arg" ] 2>/dev/null && [ "$arg" -gt 255 ]; then
            printf "ip: invalid argument '%s' to 'table'\n" "$arg" >&2; return 1
        fi
        previous="$arg"
    done
    family="${1#-}"
    if [ "$2" = rule ]; then
        [ "$family" != 6 ] || [ "${IPV6_DISABLED:-0}" = 0 ] || return 1
        touch "$TEST_ROOT/rules-$family"
        action="$3"
        if [ "$action" = show ]; then
            cat "$TEST_ROOT/rules-$family"
        elif [ "$action" = add ] || [ "$action" = del ]; then
            shift 3
            address=''; mark=''; priority=''; table=''
            while [ "$#" -gt 0 ]; do
                case "$1" in
                    priority) priority="$2" ;;
                    to) address="$2" ;;
                    fwmark) mark="$2" ;;
                    table) table="$2" ;;
                    iif) [ "$2" = lo ] || return 1 ;;
                    *) return 1 ;;
                esac
                shift 2
            done
            if [ -n "$address" ]; then
                # ip normalizes IPv6 spelling in the actual kernel's rule dump.
                case "$address" in *:*) address="$("$TEST_PYTHON" -c 'import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).ip.compressed)' "$address")" ;; esac
                rule="$priority: from all to $address iif lo lookup $table"
            else
                rule="$priority: from all fwmark $mark lookup $table"
            fi
            if [ "$action" = add ]; then
                [ "${RULE_ADD_FAIL:-}" != "$address" ] || return 1
                printf '%s\n' "$rule" >>"$TEST_ROOT/rules-$family"
            else
                awk -v rule="$rule" '$0==rule { found=1; next } { print } END { if(!found) exit 1 }' "$TEST_ROOT/rules-$family" >"$TEST_ROOT/rules-new-$family" || return 1
                mv "$TEST_ROOT/rules-new-$family" "$TEST_ROOT/rules-$family"
            fi
            return 0
        fi
    fi
    case "$*" in
        '-6 rule show') [ "${IPV6_DISABLED:-0}" = 0 ] || return 1 ;;
        '-4 route show table main default dev pppoe-wan')
            [ "${WAN_DOWN:-0}" = 0 ] && echo 'default via 10.0.0.1 dev pppoe-wan' ;;
        '-4 rule show')
            case "${TABLE_CONFLICT:-}" in
                rule) echo '105: from all fwmark 0x10000 lookup 210' ;;
                priority) echo '104: from all fwmark 0x10000 lookup 105' ;;
            esac ;;
        '-4 route show table 210')
            case "${TABLE_QUERY_ERROR:-}" in
                busybox) echo 'Dump terminated' >&2; return 2 ;;
                denied) echo 'RTNETLINK answers: Operation not permitted' >&2; return 2 ;;
            esac
            if [ "${TABLE_MISSING:-0}" = 1 ]; then echo 'Error: ipv4: FIB table does not exist.' >&2; return 2; fi
            [ "${TABLE_CONFLICT:-}" != route ] || echo 'default via 192.0.2.1 dev other-wan' ;;
        '-4 route show table all')
            [ "${ALL_TABLES_FAIL:-0}" = 0 ] || { echo 'Dump terminated' >&2; return 2; }
            echo 'default via 10.0.0.1 dev pppoe-wan table main'
            [ "${ALL_TABLES_FOREIGN:-0}" = 0 ] || echo "default via 192.0.2.1 dev other-wan table ${ALL_TABLES_NAME:-210}" ;;
    esac
    return 0
}
nft() {
    printf '%s\n' "$*" >>"$TEST_ROOT/nft"
    case "$*" in
        'list tables')
            printf 'table inet PodkopTable\n'
            [ ! -f "$TEST_ROOT/legacy-nft" ] || printf 'table inet owrt_remote_wan\n'
            [ ! -f "$TEST_ROOT/foreign-nft" ] || printf 'table inet vpn_other\n'
            return 0 ;;
        'list table inet PodkopTable') printf 'meta mark set 0x00100000\n'; return 0 ;;
        'list chain inet PodkopTable '*) printf 'counter packets 12 bytes 1200\n'; return 0 ;;
        'list table inet owrt_remote_wan') test -f "$TEST_ROOT/legacy-nft" && cat "$TEST_ROOT/legacy-nft" ;;
        'list table inet vpn_other') test -f "$TEST_ROOT/foreign-nft" && cat "$TEST_ROOT/foreign-nft" ;;
        'delete table inet owrt_remote_wan')
            [ "${NFT_FAIL:-0}" = 0 ] || return 1
            rm -f "$TEST_ROOT/legacy-nft" ;;
        *) return 1 ;;
    esac
}
nslookup() {
    printf '%s\n' "$*" >>"$TEST_ROOT/dns"
    [ "${DNS_FAIL:-0}" = 0 ] || return 1
    printf 'Server: 1.1.1.1\nAddress: 1.1.1.1:53\n\nName: hub.example\nAddress 1: %s\n' "${DNS_ANSWER:-193.233.82.38}"
}
curl() { printf '<%s>\n' "$@" >"$TEST_ROOT/curl"; }
'''


class HostPolicyTests(unittest.TestCase):
    def row(self, mode="manual", host="193.233.82.38"):
        return {"id": "test-router", "vps_host": "hub.example", "public_url": "https://hub.example", "status_json": json.dumps({"vps_host_mode": mode, "vps_host": host})}

    def test_manual_heartbeat_never_proposes_a_new_tunnel_host(self):
        for row in (self.row(), {"vps_host": "hub.example", "status": {"vps_host_mode": "manual"}}):
            update = hub.canonical_router_hub_update(row, "https://hub.example")
            self.assertNotIn("vps_host", update)
            self.assertEqual(update["hub_url"], "https://hub.example")

    def test_legacy_agent_keeps_central_update_until_policy_reported(self):
        self.assertEqual(hub.canonical_router_hub_update({"vps_host": "hub.example"})["vps_host"], "hub.example")

    def test_new_config_explicitly_defaults_to_manual(self):
        payload = hub.build_openwrt_config_payload({"id": "new", "vps_host": "193.233.82.38"}, "https://hub.example")
        self.assertEqual(payload["vps_host_mode"], "manual")

    def test_auto_heartbeat_retains_central_host(self):
        self.assertEqual(hub.canonical_router_hub_update(self.row("auto"))["vps_host"], "hub.example")

    def test_stale_http_public_url_prefers_known_https_for_heartbeat_and_rebind(self):
        for secure_source in ("app", "request", "status"):
            with self.subTest(source=secure_source):
                row = {"vps_host": "hub.example", "public_url": "http://hub.example:8088"}
                app_url = "https://hub.example:9443" if secure_source == "app" else ""
                request_url = "https://hub.example:9443" if secure_source == "request" else "http://hub.example:8088"
                if secure_source == "status":
                    row["status_json"] = json.dumps({"hub_url": "https://hub.example:9443"})
                update = hub.canonical_router_hub_update(row, app_url, request_url)
                bundle, url, _ = hub.router_current_hub_bundle(row, app_url, request_url)
                self.assertEqual(update["hub_url"], "https://hub.example:9443")
                self.assertEqual(update["public_url"], "https://hub.example:9443")
                self.assertEqual(url, update["hub_url"])
                self.assertEqual(bundle["public_url"], update["public_url"])

    def test_hub_url_selection_preserves_http_only_and_custom_https_endpoints(self):
        cases = (
            ({"public_url": "http://hub.example"}, "", "http://hub.example:8088", "http://hub.example:8088"),
            ({"public_url": "https://hub.example:9443"}, "https://hub.example", "http://hub.example:8088", "https://hub.example:9443"),
            ({"public_url": "http://private.example:8088"}, "https://other.example", "", "http://private.example:8088"),
        )
        for row, app_url, request_url, expected in cases:
            with self.subTest(expected=expected):
                update = hub.canonical_router_hub_update(row, app_url, request_url)
                self.assertEqual(update["hub_url"], expected)
                self.assertEqual(hub.router_current_hub_bundle(row, app_url, request_url)[1], expected)

    def test_rebind_prefers_manual_reported_ip_over_public_hostname(self):
        bundle, url, host = hub.router_current_hub_bundle(self.row(), "https://hub.example")
        self.assertEqual(host, "193.233.82.38")
        self.assertEqual(url, "https://hub.example")
        self.assertEqual(bundle["vps_host"], host)

    def test_mode_reads_row_and_api_status_and_handles_invalid_json(self):
        for status in ("not-json", "[]", "null"):
            self.assertEqual(hub.router_reported_connection({"status_json": status, "vps_host": "193.233.82.38"}), ("auto", "193.233.82.38"))
        self.assertEqual(hub.router_reported_connection({"status": {"vps_host_mode": "auto", "vps_host": "hub.example"}}), ("auto", "hub.example"))

    def test_ui_validates_host_before_queueing(self):
        for host in ("", "https://hub.example", "host:8443", "x'; reboot", "bad..domain", "-bad.host", "999.999.999.999"):
            with self.assertRaises(ValueError):
                hub.validate_router_vps_host(host)

    def test_hub_ui_endpoint_requires_owner_and_queues_without_ssh(self):
        from types import SimpleNamespace
        db = Path(STATE.name) / "policy-api.db"
        with hub.connect(db) as conn:
            hub.init_db(conn)
            hub.upsert_router(conn, {"id": "test", "vps_host": "old.example"})
            conn.execute('update routers set status_json=? where id=?', (json.dumps({'xray_control': 1, 'vps_host_control': 1}), 'test'))
        handler = object.__new__(hub.Handler)
        handler.server = SimpleNamespace(app=SimpleNamespace(conn=lambda: hub.connect(db)))
        handler.parsed = lambda: SimpleNamespace(path="/api/router/test/vps-host")
        handler.maybe_proxy_luci_absolute = lambda path: False
        handler.require_admin = lambda: True
        handler.send_json = mock.Mock()
        handler.headers = {'X-VPS-Host-Request': '1', 'Content-Type': 'application/json', 'Content-Length': '150'}
        handler.run_router_ssh_script = mock.Mock(side_effect=AssertionError("SSH must never be used"))
        handler.read_payload = mock.Mock(return_value={"vps_host": "193.233.82.38", "vps_host_mode": "manual", "request_id": "f" * 32})
        handler.require_owner = lambda **kwargs: False
        handler.do_POST()
        handler.read_payload.assert_not_called()
        handler.require_owner = lambda **kwargs: True
        handler.do_POST()
        handler.run_router_ssh_script.assert_not_called()
        self.assertEqual(handler.send_json.call_args.args[0], 202)
        with hub.connect(db) as conn:
            row = hub.get_router(conn, "test")
            self.assertEqual(row["vps_host"], "old.example")
            self.assertEqual(hub.row_to_router(row)['vps_host_update']['status'], 'queued')


@unittest.skipUnless(SH, "a POSIX shell (Git for Windows or Linux) is required")
class RouterShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owrt-shell-tests-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        (self.directory / "uci").write_text("", encoding="utf-8")
        self.agent = (ROOT / "files/usr/sbin/owrt-remote").read_text(encoding="utf-8").split('cmd="${1:-help}"')[0]
        self.env = {**os.environ, "TEST_ROOT": self.directory.as_posix(), "TMPDIR": self.directory.as_posix(), "TEST_PYTHON": Path(sys.executable).as_posix(), "OWRT_REMOTE_WAN_HELPER": (ROOT / "files/usr/lib/owrt-remote/wan.sh").as_posix()}

    def config(self, **kwargs):
        (self.directory / "uci").write_text("".join(f"owrtremote.main.{key}={value}\n" for key, value in kwargs.items()), encoding="utf-8")

    def run_shell(self, commands, **env):
        return subprocess.run([SH], input=self.agent + "\n" + MOCKS + "\n" + commands, encoding="utf-8", capture_output=True, env={**self.env, **env}, cwd=ROOT, timeout=60 if os.name == "nt" else 30)

    def test_old_and_explicit_manual_modes_ignore_host_rewrite(self):
        response = {"hub_update": {"vps_host": "hub.example", "hub_url": "https://hub.example", "public_url": "https://hub.example"}}
        (self.directory / "response").write_text(json.dumps(response), encoding="utf-8")
        diagnostics = ""
        for mode in (None, "manual", "invalid"):
            values = {"vps_host": "193.233.82.38", "hub_url": "https://hub.example", "public_url": "https://hub.example"}
            if mode: values["vps_host_mode"] = mode
            self.config(**values)
            result = self.run_shell('heartbeat_apply_hub_update "$TEST_ROOT/response" https://hub.example; uci_get vps_host ""')
            diagnostics += result.stderr
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "193.233.82.38")
        self.assertIn("reason=manual", diagnostics)
        self.assertFalse((self.directory / "log").exists(), "CLI diagnostics are not archived")

    def test_explicit_auto_changes_host_and_logs_old_new_reason(self):
        self.config(vps_host="193.233.82.38", vps_host_mode="auto")
        (self.directory / "response").write_text(json.dumps({"hub_update": {"vps_host": "hub.example"}}), encoding="utf-8")
        result = self.run_shell('heartbeat_apply_hub_update "$TEST_ROOT/response" https://hub.example; uci_get vps_host ""')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "hub.example")
        self.assertIn("old=193.233.82.38 new=hub.example reason=hub_update mode=auto", result.stderr)

    def test_heartbeat_never_downgrades_configured_https_in_manual_or_auto_mode(self):
        (self.directory / "response").write_text(json.dumps({"hub_update": {
            "hub_url": "http://hub.example", "public_url": "http://hub.example",
        }}), encoding="utf-8")
        diagnostics = ""
        for mode in ("manual", "auto"):
            with self.subTest(mode=mode):
                self.config(vps_host_mode=mode, vps_host="193.233.82.38",
                            hub_url="https://hub.example:9443", public_url="https://hub.example:9443")
                result = self.run_shell('''
heartbeat_apply_hub_update "$TEST_ROOT/response" http://hub.example:8088
heartbeat_apply_hub_update "$TEST_ROOT/response" http://hub.example:8088
printf '%s|%s|%s' "$(uci_get hub_url '')" "$(uci_get public_url '')" "$(uci_get vps_host '')"
''')
                diagnostics += result.stderr
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "https://hub.example:9443|https://hub.example:9443|193.233.82.38")
        self.assertFalse((self.directory / "commits").exists())
        self.assertEqual(diagnostics.count("reason=https-downgrade"), 2)
        self.assertFalse((self.directory / "log").exists(), "rejected proposals are not archived")

    def test_successful_fallback_heartbeat_does_not_replace_configured_hub_url(self):
        self.config(hub_url="https://hub.example", public_url="https://backup.example", hub_token="test-token", vps_host="193.233.82.38")
        result = self.run_shell('''
heartbeat_json() { printf '{"id":"test-router"}'; }
post_json() {
    case "$1" in
        https://backup.example/api/heartbeat) printf '{"ok":true}' ;;
        *) return 1 ;;
    esac
}
send_heartbeat >/dev/null || exit
printf '%s' "$(uci_get hub_url '')"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "https://hub.example")
        self.assertFalse((self.directory / "commits").exists())

    def test_heartbeat_still_upgrades_http_and_supports_http_only_installations(self):
        for current, proposed in (("http://hub.example:8088", "https://hub.example:9443"),
                                  ("http://old.example:8088", "http://new.example:8088")):
            with self.subTest(proposed=proposed):
                self.config(hub_url=current, public_url=current)
                (self.directory / "response").write_text(json.dumps({"hub_update": {
                    "hub_url": proposed, "public_url": proposed,
                }}), encoding="utf-8")
                result = self.run_shell('heartbeat_apply_hub_update "$TEST_ROOT/response" http://fallback.example; printf "%s|%s" "$(uci_get hub_url \'\')" "$(uci_get public_url \'\')"')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"{proposed}|{proposed}")

    def test_hub_generated_rebind_preserves_manual_ip_and_wan_options(self):
        self.config(vps_host="193.233.82.38", wan_direct="1", wan_dns="9.9.9.9", wan_interface="internet")
        payload = hub.build_openwrt_config_payload(self.row(), "https://hub.example")
        script = "\n".join(hub.build_openwrt_config_lines(payload, mode="ssh-apply"))
        result = self.run_shell(script + '\nprintf "host=%s wan=%s dns=%s iface=%s" "$(uci_get vps_host "")" "$(uci_get wan_direct "")" "$(uci_get wan_dns "")" "$(uci_get wan_interface "")"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("host=193.233.82.38 wan=1 dns=9.9.9.9 iface=internet", result.stdout)

    def row(self):
        return {"id": "test-router", "name": "test", "vps_host": "hub.example", "reverse_tag": "test-in"}

    def test_wan_routes_only_local_hub_and_dns_without_nft_marking(self):
        self.config(vps_host="193.233.82.38", hub_url="https://hub.example", vps_host_mode="manual")
        result = self.run_shell("wan_prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        rules = (self.directory / "rules-4").read_text().splitlines()
        self.assertEqual(set(rules), {"104: from all to 193.233.82.38 iif lo lookup 210", "104: from all to 1.1.1.1 iif lo lookup 210"})
        self.assertTrue(all(" iif lo " in rule for rule in rules), "forwarded br-lan traffic must not match")
        self.assertNotIn("mark set", (self.directory / "nft").read_text())
        routes = (self.directory / "ip").read_text()
        self.assertIn("via 10.0.0.1 dev pppoe-wan onlink table 210", routes)
        self.assertIn("priority 104 iif lo to 193.233.82.38 table 210", routes)
        self.assertIn("unreachable default metric 42760 table 210", routes)
        self.assertFalse((self.directory / "dns").exists(), "an IP pin does not need DNS")

    def test_busybox_large_table_failure_is_detected_before_route_mutation(self):
        self.config(vps_host="193.233.82.38")
        result = self.run_shell("WAN_TABLE=20110; wan_preflight")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid argument '20110' to 'table'", result.stderr)
        self.assertNotIn("route replace", (self.directory / "ip").read_text())
        self.assertFalse((self.directory / "nft").exists())
        result = self.run_shell("wan_prepare")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_foreign_table_or_priority_is_preserved_on_failure_and_cleanup(self):
        self.config(vps_host="193.233.82.38")
        for conflict in ("rule", "priority", "route"):
            result = self.run_shell("wan_prepare", TABLE_CONFLICT=conflict)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("WAN IPv4 table 210", result.stderr)
            self.assertNotIn("route replace", (self.directory / "ip").read_text())
            nft_log = self.directory / "nft"
            self.assertNotIn("-f", nft_log.read_text() if nft_log.exists() else "")
            result = self.run_shell("wan_cleanup", TABLE_CONFLICT=conflict)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("route flush", (self.directory / "ip").read_text())

    def test_cleanup_removes_routes_owned_during_partial_prepare_failure(self):
        self.config(vps_host="193.233.82.38")
        (self.directory / "legacy-nft").write_text("meta mark set 0x40000000\n")
        result = self.run_shell("wan_prepare", NFT_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        result = self.run_shell("wan_cleanup")
        self.assertEqual(result.returncode, 0, result.stderr)
        routes = (self.directory / "ip").read_text()
        self.assertIn("-4 route flush table 210", routes)
        self.assertIn("-6 route flush table 210", routes)
        self.assertFalse((self.directory / "owrt-remote-wan/route-owner-4").exists())

    def test_cleanup_preserves_foreign_rule_with_different_mark_mask(self):
        self.config(vps_host="193.233.82.38")
        foreign = "104: from all fwmark 0x40000000/0x40000000 lookup 210\n"
        (self.directory / "rules-4").write_text(foreign)
        result = self.run_shell("wan_prepare")
        self.assertNotEqual(result.returncode, 0)
        result = self.run_shell("wan_cleanup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.directory / "rules-4").read_text(), foreign)
        log = (self.directory / "ip").read_text()
        self.assertNotIn("rule del", log)
        self.assertNotIn("route flush", log)

    def test_ipv4_prepares_with_disabled_ipv6_and_missing_iproute2_table(self):
        self.config(vps_host="193.233.82.38")
        result = self.run_shell("wan_prepare", IPV6_DISABLED="1", TABLE_MISSING="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        routes = (self.directory / "ip").read_text()
        self.assertIn("-4 route replace default", routes)
        self.assertNotIn("-6 route replace", routes)

    def test_curl_preserves_https_hostname_and_uses_pinned_ip(self):
        self.config(vps_host="193.233.82.38", vps_host_mode="manual")
        result = self.run_shell('wan_prepare && wan_post_json https://hub.example/api/heartbeat test-token "$TEST_ROOT/payload"')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.directory / "curl").read_text()
        self.assertIn("<hub.example:443:193.233.82.38>", args)
        self.assertIn("<pppoe-wan>", args)
        self.assertIn("<https://hub.example/api/heartbeat>", args)
        self.assertNotIn("<-k>", args)
        self.assertIn("<--max-time>", args)
        self.assertIn("<--resolve>", args)
        self.assertIn("<--data-binary>", args)
        self.assertNotIn("<--location-trusted>", args)
        self.assertNotIn("<--location>", args)

    def test_busybox_empty_table_is_confirmed_before_wan_start(self):
        self.config(vps_host="193.233.82.38", hub_url="https://hub.example", wan_direct="1", vless_uuid="11111111-1111-4111-8111-111111111111")
        result = self.run_shell('render_client_config --stdout', TABLE_QUERY_ERROR="busybox")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["outbounds"][2]["settings"]["address"], "193.233.82.38")
        routes = self.directory.joinpath("ip").read_text()
        self.assertIn("-4 route show table all", routes)
        self.assertIn("-4 route replace default", routes)
        self.assertEqual(self.directory.joinpath("owrt-remote-wan/active-interface").read_text().strip(), "wan")

    def test_table_dump_error_does_not_hide_foreign_routes_or_read_failures(self):
        self.config(vps_host="193.233.82.38")
        for settings in ({"TABLE_QUERY_ERROR": "busybox", "ALL_TABLES_FOREIGN": "1"},
                         {"TABLE_QUERY_ERROR": "busybox", "ALL_TABLES_FAIL": "1"},
                         {"TABLE_QUERY_ERROR": "denied"}):
            with self.subTest(settings=settings):
                result = self.run_shell('wan_prepare', **settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("route replace", self.directory.joinpath("ip").read_text())

    def test_table_dump_fallback_recognizes_named_foreign_table(self):
        self.config(vps_host="193.233.82.38")
        result = self.run_shell('''
wan_table_names() { printf '210\nremote_named\n'; }
wan_prepare
''', TABLE_QUERY_ERROR="busybox", ALL_TABLES_FOREIGN="1", ALL_TABLES_NAME="remote_named")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not belong to Remote", result.stderr)
        self.assertNotIn("route replace", self.directory.joinpath("ip").read_text())

    def test_xray_is_not_executed_with_a_failed_or_empty_configuration(self):
        executable = self.directory / "fake-xray"
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"$TEST_ROOT/xray-called"\n', encoding="utf-8", newline="\n")
        executable.chmod(0o755)
        self.config(enabled="1", xray_bin=executable.as_posix())
        for render in ('return 7', 'return 0', 'printf "%s" "$TEST_ROOT/missing-config"'):
            with self.subTest(render=render):
                result = self.run_shell('ensure_xray_available_with_retry() { return 0; }; render_client_config() { ' + render + '; }; run_xray')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.directory.joinpath("xray-called").exists())

    def test_xray_runs_with_the_successfully_rendered_configuration(self):
        executable = self.directory / "fake-xray"
        executable.write_text('#!/bin/sh\n[ "$1" != help ] || { echo run; exit; }; printf "%s\\n" "$*" >"$TEST_ROOT/xray-called"\n', encoding="utf-8", newline="\n")
        executable.chmod(0o755)
        config = self.directory / "valid-config.json"
        config.write_text('{"outbounds": []}')
        self.config(enabled="1", xray_bin=executable.as_posix())
        result = self.run_shell('ensure_xray_available_with_retry() { return 0; }; render_client_config() { printf "%s" "$TEST_ROOT/valid-config.json"; }; run_xray')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.directory.joinpath("xray-called").read_text().strip(), 'run -config ' + config.as_posix())

    def test_plain_http_heartbeat_uses_controlled_authenticated_posts(self):
        self.config(vps_host="193.233.82.38", vps_host_mode="manual")
        result = self.run_shell('''
curl() {
    printf '<%s>\n' "$@" >"$TEST_ROOT/curl"
    printf '{"ok":true}'
}
post_json http://hub.example/api/heartbeat test-token "$TEST_ROOT/payload"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.directory / "curl").read_text()
        self.assertIn("<Authorization: Bearer test-token>", args)
        self.assertIn("<--data-binary>", args)
        self.assertNotIn("<--location>", args)

    def test_literal_ip_fallback_is_not_rewritten_to_a_different_pin(self):
        self.config(vps_host="193.233.82.38", vps_host_mode="manual")
        result = self.run_shell('wan_endpoint_ip http://192.0.2.5:8088/api/heartbeat')
        self.assertEqual(result.stdout, "192.0.2.5")

    def test_wan_refresh_does_not_accumulate_policy_rules(self):
        self.config(vps_host="193.233.82.38", hub_url="https://hub.example")
        result = self.run_shell('wan_prepare && wan_prepare && wan_prepare')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.directory / "ip").read_text().count("rule add"), 2)

    def test_migration_passes_original_podkop_diagnostic_and_keeps_foreign_warnings(self):
        self.config(vps_host="193.233.82.38", wan_direct="1")
        legacy = self.directory / "legacy-nft"
        legacy.write_text("meta mark set 0x40000000\n")
        (self.directory / "rules-4").write_text("104: from all fwmark 0x40000000 lookup 210\n105: from all fwmark 0x100000/0x100000 lookup podkop\n")
        # Execute upstream's unmodified diagnostic logic; only isolate its temp path.
        diagnostic = (ROOT / "tests/fixtures/podkop-0.7.22-check-nft.sh").read_text().replace("/tmp/podkop_mark_check", '"$TEST_ROOT"/podkop_mark_check')
        setup = diagnostic + '\nNFT_TABLE_NAME=PodkopTable; jq() { cat; }; sleep() { :; }\n'
        result = self.run_shell(setup + 'check_nft_rules; wan_prepare || exit; check_nft_rules')
        self.assertEqual(result.returncode, 0, result.stderr)
        before, after = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(before["rules_other_mark_exist"], 1)
        self.assertEqual(after["rules_other_mark_exist"], 0)
        self.assertTrue(all(value == 1 for key, value in after.items() if key != "rules_other_mark_exist"))
        self.assertFalse(legacy.exists())
        rules = (self.directory / "rules-4").read_text()
        self.assertNotIn("0x40000000", rules)
        self.assertIn("105: from all fwmark 0x100000/0x100000 lookup podkop", rules)
        (self.directory / "foreign-nft").write_text("meta mark set 0x10\n")
        result = self.run_shell(setup + 'wan_prepare || exit; check_nft_rules')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["rules_other_mark_exist"], 1)
        self.assertTrue((self.directory / "foreign-nft").exists())

    def test_changed_hub_keeps_running_xray_destination_and_prunes_stale_http_ip(self):
        config = self.directory / "xray.json"
        config.write_text(json.dumps({"outbounds": [{"tag": "vps-interconn", "settings": {"address": "193.233.82.38"}}]}))
        self.config(vps_host="192.0.2.2", public_url="https://192.0.2.3:9443", xray_config=config.as_posix())
        (self.directory / "rules-4").write_text("104: from all to 192.0.2.99 iif lo lookup 210\n")
        result = self.run_shell('wan_prepare')
        self.assertEqual(result.returncode, 0, result.stderr)
        rules = (self.directory / "rules-4").read_text()
        self.assertIn("193.233.82.38 iif lo", rules)
        self.assertIn("192.0.2.2 iif lo", rules)
        self.assertIn("192.0.2.3 iif lo", rules)
        self.assertNotIn("192.0.2.99", rules)
        self.assertIn("192.0.2.3 9443", (self.directory / "owrt-remote-wan/endpoints").read_text())
        config.write_text(json.dumps({"outbounds": [{"tag": "vps-interconn", "settings": {"address": "192.0.2.2"}}]}))
        result = self.run_shell('wan_prepare && wan_cleanup')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.directory / "rules-4").read_text(), "")

    def test_ipv6_compression_does_not_duplicate_rules(self):
        self.config(vps_host="2001:0db8:0:0:0:0:0:1", wan_dns="2001:4860:4860::8888")
        result = self.run_shell('wan_prepare && wan_prepare; wan_address_key ::1; wan_address_key 2001:db8::; wan_address_key ::')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["0:0:0:0:0:0:0:1", "2001:db8:0:0:0:0:0:0", "0:0:0:0:0:0:0:0"])
        rules = (self.directory / "rules-6").read_text().splitlines()
        self.assertEqual(len(rules), 2)
        self.assertEqual((self.directory / "ip").read_text().count("rule add"), 2)

    def test_failed_replacement_retains_previous_direct_destination(self):
        self.config(vps_host="192.0.2.2")
        (self.directory / "rules-4").write_text("104: from all to 192.0.2.1 iif lo lookup 210\n")
        result = self.run_shell('wan_prepare', RULE_ADD_FAIL="192.0.2.2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("192.0.2.1 iif lo", (self.directory / "rules-4").read_text())
        self.assertFalse((self.directory / "owrt-remote-wan/lock").exists())

    def test_dns_failure_does_not_fall_back_to_system_resolver(self):
        self.config(vps_host="hub.example")
        result = self.run_shell('wan_resolve_host hub.example', DNS_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.directory / "dns").read_text().count("nslookup"), 0)
        self.assertIn("hub.example 1.1.1.1", (self.directory / "dns").read_text())

    def test_wan_dns_ignores_local_fakeip_and_uses_uplink_resolver(self):
        self.config(vps_host="hub.example", wan_dns="9.9.9.9")
        result = self.run_shell('wan_prepare; wan_resolve_host hub.example')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "193.233.82.38")
        self.assertIn("hub.example 9.9.9.9", (self.directory / "dns").read_text())
        result = self.run_shell('rm -f "$WAN_STATE"/resolve-*; wan_resolve_host hub.example', DNS_ANSWER="198.18.1.5")
        self.assertNotEqual(result.returncode, 0)

    def test_wan_prepare_fails_without_wan_and_releases_lock_on_failure(self):
        self.config(vps_host="193.233.82.38")
        (self.directory / "legacy-nft").write_text("meta mark set 0x40000000\n")
        for env in ({"WAN_DOWN": "1"}, {"NFT_FAIL": "1"}):
            result = self.run_shell("wan_prepare", **env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.directory / "owrt-remote-wan/lock").exists())

    def test_xray_only_reverse_sockets_are_bound_to_wan(self):
        self.config(vps_host="193.233.82.38", wan_direct="1", vless_uuid="11111111-1111-4111-8111-111111111111")
        result = self.run_shell("render_client_config --stdout")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config['log'], {'access': 'none', 'error': '', 'loglevel': 'warning'})
        for outbound in config["outbounds"]:
            if outbound["tag"].startswith("vps-"):
                self.assertEqual(outbound["streamSettings"]["sockopt"], {"mark": 2097152, "interface": "pppoe-wan"})
                self.assertEqual(outbound["settings"]["address"], "193.233.82.38")
            else:
                self.assertNotIn("streamSettings", outbound)

    def test_cgi_saves_manual_auto_and_wan_flags_without_exposing_secrets(self):
        cgi = (ROOT / "files/www/cgi-bin/owrt-remote").read_text(encoding="utf-8").split('\nread_request\n')[0]
        self.config(vps_host="193.233.82.38", vps_host_mode="auto")
        commands = cgi + '''
param() {
  case "$1" in
    vps_host) echo 193.233.82.38 ;;
    wan_interface) echo wan ;;
    *) printf '' ;;
  esac
}
has_param() { [ "$1" = wan_direct ]; }
save_config
printf '%s %s' "$(uci_get vps_host_mode '')" "$(uci_get wan_direct '')"
has_param() { [ "$1" = vps_host_auto ]; }
save_config
printf ' %s %s' "$(uci_get vps_host_mode '')" "$(uci_get wan_direct '')"
'''
        result = self.run_shell(commands)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "manual 1 auto 0")


if __name__ == "__main__":
    unittest.main()
