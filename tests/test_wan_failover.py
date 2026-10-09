"""Selected-uplink failover with mocked OpenWrt netifd and kernel routes."""
import json
import os
from pathlib import Path
import unittest

import test_host_policy as host_policy

SH = host_policy.SH


UPLINK_MOCKS = r'''
ubus() {
    local iface up device dns
    iface="${2#network.interface.}"
    up=1; device=''; dns=''
    case "$iface" in
        wan) [ ! -e "$TEST_ROOT/wan-down" ] || up=0; device=eth0; dns=1.1.1.1 ;;
        wwan) [ ! -e "$TEST_ROOT/wwan-down" ] || up=0; device=wlan1; dns=9.9.9.9 ;;
        modem4g) [ ! -e "$TEST_ROOT/modem-down" ] || up=0; device=wwan0; dns=8.8.8.8 ;;
        proxy) device=tun0; dns=127.0.0.1 ;;
        *) return 1 ;;
    esac
    [ "$up" = 1 ] || device=''
    printf '{"up":%s,"l3_device":"%s","dns-server":["%s"]}' "$up" "$device" "$dns"
}
jsonfilter() {
    if [ "$1" = -i ]; then
        "$TEST_PYTHON" "$TEST_ROOT/jsonfilter.py" "$2" "$4"
        return
    fi
    "$TEST_PYTHON" "$TEST_ROOT/jsonfilter.py" - "$2"
}
ip() {
    printf '%s\n' "$*" >>"$TEST_ROOT/ip"
    case "$*" in
        '-4 route show table main default dev eth0') [ -e "$TEST_ROOT/wan-down" ] || echo 'default via 192.0.2.1 dev eth0' ;;
        '-4 route show table main default dev wlan1') [ -e "$TEST_ROOT/wwan-down" ] || echo 'default via 192.0.2.2 dev wlan1' ;;
        '-4 route show table main default dev wwan0') [ -e "$TEST_ROOT/modem-down" ] || echo 'default dev wwan0' ;;
        '-4 route show table main default dev tun0') echo 'default dev tun0' ;;
        '-6 route show table main default dev '*) return 0 ;;
        '-6 rule show') return 0 ;;
        '-4 rule show') return 0 ;;
    esac
    return 0
}
'''

JSONFILTER = '''import json, re, sys
data = json.load(sys.stdin if sys.argv[1] == "-" else open(sys.argv[1]))
query = sys.argv[2]
if query == "@.l3_device":
    value = data.get("l3_device", "")
elif query == '@["dns-server"][0]':
    value = next(iter(data.get("dns-server", [])), "")
else:
    tag = re.search(r'tag="([^"]+)"', query).group(1)
    row = next((row for row in data.get("outbounds", []) if row.get("tag") == tag), {})
    if query.endswith(".interface"):
        value = row.get("streamSettings", {}).get("sockopt", {}).get("interface", "")
    else:
        value = row.get("settings", {}).get("address", "")
sys.stdout.buffer.write(str(value).encode())
'''


@unittest.skipUnless(SH, "A POSIX shell is required")
class WanFailoverTests(unittest.TestCase):
    setUp = host_policy.RouterShellTests.setUp
    config = host_policy.RouterShellTests.config
    run_shell = host_policy.RouterShellTests.run_shell

    def prepare(self, interfaces="wan wwan modem4g"):
        self.config(wan_interface=interfaces, wan_direct="1", vps_host="192.0.2.10", vps_host_mode="manual",
                    hub_url="https://hub.example", hub_token="test-token", vless_uuid="test-uuid")
        self.directory.joinpath("jsonfilter.py").write_text(JSONFILTER, encoding="utf-8")

    def shell(self, commands):
        return self.run_shell(UPLINK_MOCKS + "\n" + commands)

    def test_down_primary_selects_wifi_and_uses_its_dns_and_routes(self):
        self.prepare()
        self.directory.joinpath("wan-down").touch()
        result = self.shell('wan_prepare && printf "active=%s device=%s dns=%s" "$(cat "$WAN_STATE/active-interface")" "$(wan_device)" "$(wan_dns_server)"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "active=wwan device=wlan1 dns=9.9.9.9")
        self.assertIn("via 192.0.2.2 dev wlan1 onlink table 210", self.directory.joinpath("ip").read_text())

    def test_third_uplink_is_used_when_first_two_are_down(self):
        self.prepare()
        self.directory.joinpath("wan-down").touch()
        self.directory.joinpath("wwan-down").touch()
        result = self.shell('wan_prepare && printf "%s %s" "$(wan_device)" "$(wan_dns_server)"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "wwan0 8.8.8.8")

    def test_healthy_backup_is_retained_when_primary_returns(self):
        self.prepare()
        result = self.shell('touch "$TEST_ROOT/wan-down"; wan_prepare || exit; rm "$TEST_ROOT/wan-down"; wan_prepare || exit; cat "$WAN_STATE/active-interface"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "wwan")

    def test_failover_rebinds_rendered_reverse_connections_and_heartbeat(self):
        self.prepare()
        result = self.shell('wan_prepare || exit; touch "$TEST_ROOT/wan-down"; render_client_config --stdout')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        for outbound in config["outbounds"]:
            if outbound["tag"].startswith("vps-"):
                self.assertEqual(outbound["streamSettings"]["sockopt"]["interface"], "wlan1")
            else:
                self.assertNotIn("streamSettings", outbound)
        result = self.shell('wan_post_json https://hub.example/api/heartbeat test-token "$TEST_ROOT/payload"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<wlan1>", self.directory.joinpath("curl").read_text())
        self.assertIn("WAN failover: wan -> wwan", self.directory.joinpath("log").read_text())

    def test_only_selected_real_uplinks_are_used_and_all_down_fails(self):
        self.prepare("proxy wan")
        result = self.shell('printf "%s" "$(wan_select_interface)"')
        self.assertEqual(result.stdout, "wan")
        self.directory.joinpath("wan-down").touch()
        result = self.shell("wan_prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.directory.joinpath("owrt-remote-wan/lock").exists())
        self.assertFalse(self.directory.joinpath("owrt-remote-wan/active-interface").exists())

    def test_xray_reconnect_is_requested_only_for_a_changed_binding(self):
        self.prepare()
        config = self.directory / "xray.json"
        config.write_text(json.dumps({"outbounds": [{"tag": "vps-interconn", "streamSettings": {"sockopt": {"interface": "eth0"}}}]}))
        self.config(wan_interface="wan wwan", wan_direct="1", vps_host="192.0.2.10", xray_config=config.as_posix())
        result = self.shell('''
xray_running() { return 0; }
wan_reconnect_xray() { printf '%s -> %s\n' "$1" "$2" >>"$TEST_ROOT/reconnect"; }
wan_prepare || exit
wan_reconcile_xray || exit
touch "$TEST_ROOT/wan-down"
wan_prepare || exit
wan_reconcile_xray
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.directory.joinpath("reconnect").read_text(), "eth0 -> wlan1\n")

    def test_concurrent_reconnect_requests_restart_once_and_release_lock(self):
        self.prepare()
        restart = self.directory / "restart"
        restart.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"$TEST_ROOT/restarts"\n', encoding="utf-8", newline="\n")
        restart.chmod(0o755)
        original = "/etc/init.d/owrt-remote restart >/dev/null 2>&1 || log 'WAN failover tunnel restart failed'"
        self.assertIn(original, self.agent)
        self.agent = self.agent.replace(original, '"$TEST_ROOT/restart" restart >/dev/null 2>&1')
        result = self.shell('wan_reconnect_xray eth0 wlan1; wan_reconnect_xray eth0 wlan1; sleep 2')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.directory.joinpath("restarts").read_text(), "restart\n")
        self.assertFalse(self.directory.joinpath("owrt-remote-wan-restart").exists())

    def test_hotplug_handles_selected_wifi_and_modem_events_and_ignores_others(self):
        self.prepare()
        self.config(enabled="1", wan_direct="1", wan_interface="wan wwan modem4g")
        hotplug = (host_policy.ROOT / "files/etc/hotplug.d/iface/99-owrt-remote").read_text(encoding="utf-8")
        hotplug = hotplug.replace('/usr/sbin/owrt-remote', 'test_hotplug_agent').replace('/etc/init.d/owrt-remote', 'test_hotplug_restart')
        callbacks = r'''
sleep() { :; }
test_hotplug_agent() { printf '%s\n' "$*" >>"$TEST_ROOT/hotplug-calls"; }
test_hotplug_restart() { printf restart >>"$TEST_ROOT/hotplug-calls"; }
'''
        for iface, action, expected in (("wwan", "ifup", True), ("modem4g", "ifdown", True), ("lan", "ifup", False)):
            with self.subTest(iface=iface, action=action):
                calls = self.directory / "hotplug-calls"
                calls.unlink(missing_ok=True)
                result = self.run_shell(callbacks + '\n' + hotplug, INTERFACE=iface, ACTION=action, DEVICE="wlan1")
                self.assertEqual(result.returncode, 0, result.stderr)
                if expected:
                    self.assertEqual(calls.read_text(), "heartbeat wan-reconnect\n")
                else:
                    self.assertFalse(calls.exists())


if __name__ == "__main__":
    unittest.main()
