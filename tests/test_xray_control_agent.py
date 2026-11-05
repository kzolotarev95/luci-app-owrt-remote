"""Exercise the actual shell control helper with simulated procd and Xray."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_host_policy as base

ROOT = base.ROOT
BUSYBOX = ROOT / '.test-output/busybox/busybox64.exe'
SHELL = str(BUSYBOX) if BUSYBOX.exists() else base.SH

MOCKS = r'''
jsonfilter() {
    if [ "$1" = -i ]; then source="$2"; expr="$4"; else source=-; expr="$2"; fi
    "$TEST_PYTHON" "$TEST_ROOT/jsonfilter.py" "$source" "$expr"
}
xray_running() { [ -e "$TEST_ROOT/xray-running" ]; }
xray_bin_path() { printf '%s/bin' "$TEST_ROOT"; }
render_client_config() {
    [ ! -f "$TEST_ROOT/invalid-config" ] || { printf 'Xray rejected configuration\n' >&2; return 23; }
    printf '%s/config.json' "$TEST_ROOT"
}
stop_xray() { rm -f "$TEST_ROOT/xray-running"; }
sleep() { :; }
uptime_seconds() { printf '%s' "${NOW:-100}"; }
timeout() { shift; shift; shift; xray_control_execute "$@"; }
ubus() {
    printf '%s\n' "$*" >>"$TEST_ROOT/ubus-calls"
    case "$3" in
        list) printf '{"owrt-remote":{"instances":{"heartbeat":{"running":true},"xray":{"running":true},"control":{"running":false}}}}' ;;
        add)
            case "$4" in *xray-control-worker*) : ;; *) touch "$TEST_ROOT/xray-running" ;; esac ;;
        delete)
            [ "$4" = '{"name":"owrt-remote","instance":"xray"}' ] || return 9
            rm -f "$TEST_ROOT/xray-running" ;;
    esac
}
'''


@unittest.skipUnless(SHELL, 'POSIX shell required')
class AgentTests(unittest.TestCase):
    setUp = base.RouterShellTests.setUp
    config = base.RouterShellTests.config

    def prepare(self, action='stop'):
        self.config(enabled='1', wan_direct='1')
        self.env.update(OWRT_REMOTE_CONTROL_HELPER=(ROOT / 'files/usr/lib/owrt-remote/control.sh').as_posix(),
                        OWRT_REMOTE_CONTROL_STATE=(self.directory / 'state').as_posix(),
                        OWRT_REMOTE_CONTROL_RUNTIME=(self.directory / 'runtime').as_posix())
        binary = self.directory / 'bin'
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
        self.directory.joinpath('response').write_text(json.dumps({'ok': True, 'xray_command': {'id': 'a'*32, 'action': action}}))
        self.directory.joinpath('jsonfilter.py').write_text('''import json,re,sys
try:
    data=json.load(sys.stdin if sys.argv[1]=='-' else open(sys.argv[1]))
    for key in re.findall(r'[a-zA-Z_][a-zA-Z_0-9-]*', sys.argv[2][1:]): data=data[key]
    print(json.dumps(data) if isinstance(data,(dict,bool)) else data)
except (KeyError,ValueError,TypeError,FileNotFoundError): pass
''')

    def shell(self, commands):
        args = [SHELL, 'ash'] if 'busybox' in SHELL.lower() else [SHELL]
        return subprocess.run(args, input=self.agent + '\n' + base.MOCKS + '\n' + MOCKS + '\n' + commands,
                              encoding='utf-8', capture_output=True, cwd=ROOT, env=self.env, timeout=40)

    def test_stop_keeps_heartbeat_and_blocks_wan_auto_recovery(self):
        self.prepare()
        result = self.shell(r'''
touch "$TEST_ROOT/xray-running"
xray_control_dispatch "$TEST_ROOT/response" || exit
xray_control_worker aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa stop || exit
xray_control_stopped || exit 5
wan_reconcile_xray || exit
xray_control_result_json
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['status'], 'succeeded')
        self.assertEqual(value['xray'], 'stopped')
        calls = self.directory.joinpath('ubus-calls').read_text()
        self.assertIn('"instance":"xray"', calls)
        self.assertNotIn('"instance":"heartbeat"', calls)
        self.assertNotIn('wan-recovery', calls)

    def test_start_when_tunnel_and_ssh_are_down(self):
        self.prepare('start')
        result = self.shell(r'''
mkdir -p "$CONTROL_STATE"; touch "$CONTROL_STATE/stopped"
xray_control_dispatch "$TEST_ROOT/response" || exit
xray_control_worker aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa start || exit
xray_control_result_json
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['xray'], 'running')
        self.assertFalse(self.directory.joinpath('state/stopped').exists())

    def test_replay_does_not_restart_again_and_bad_action_never_executes(self):
        self.prepare('restart')
        result = self.shell(r'''
xray_control_dispatch "$TEST_ROOT/response" || exit
xray_control_worker aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa restart || exit
xray_control_dispatch "$TEST_ROOT/response" || exit
xray_control_dispatch "$TEST_ROOT/response" || exit
xray_control_result_json
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.directory.joinpath('ubus-calls').read_text()
        self.assertEqual(calls.count('xray-control-worker'), 1)
        self.directory.joinpath('response').write_text(json.dumps({'xray_command': {'id': 'b'*32, 'action': 'stop; touch injected'}}))
        result = self.shell('xray_control_dispatch "$TEST_ROOT/response"')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('injected', self.directory.joinpath('ubus-calls').read_text())

    def test_invalid_config_preserves_running_tunnel_and_reports_real_error(self):
        self.prepare('restart')
        result = self.shell(r'''
touch "$TEST_ROOT/xray-running" "$TEST_ROOT/invalid-config"
xray_control_dispatch "$TEST_ROOT/response" || exit
xray_control_worker aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa restart || exit
xray_control_result_json
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['status'], 'failed')
        self.assertEqual(value['rc'], 23)
        self.assertIn('Xray rejected configuration', value['message'])
        self.assertTrue(self.directory.joinpath('xray-running').exists())
        self.assertFalse(self.directory.joinpath('state/stopped').exists())

    def test_dead_worker_is_reported_without_replay_and_stale_lock_is_recovered(self):
        self.prepare()
        result = self.shell(r'''
mkdir -p "$CONTROL_RUNTIME/dispatch-lock"
printf '99999999\n' >"$CONTROL_RUNTIME/dispatch-lock/pid"
xray_control_dispatch "$TEST_ROOT/response" || exit
NOW=120
xray_control_result_json
xray_control_dispatch "$TEST_ROOT/response" || exit
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'interrupted')
        self.assertEqual(self.directory.joinpath('ubus-calls').read_text().count('xray-control-worker'), 1)

    def test_shell_timeout_without_optional_applet_returns_result_and_reaps_child(self):
        helper = (ROOT / 'files/usr/lib/owrt-remote/control.sh').read_text(encoding='utf-8')
        # Shorten the watchdog ticks, without substituting the control flow.
        helper = helper.replace('[ "$ticks" -ge 45 ]', '[ "$ticks" -ge 2 ]')
        prefix = r'''
command() { return 1; }
sleep() { "$TEST_PYTHON" -c 'import time; time.sleep(0.05)'; }
'''
        args = [SHELL, 'ash'] if 'busybox' in SHELL.lower() else [SHELL]
        commands = '''
failcmd() { return 7; }
run_xray_control_limited failcmd
printf 'FAIL_RC=%s\\n' "$?"
'''
        result = subprocess.run(args, input=helper + prefix + '\n' + commands,
                                encoding='utf-8', capture_output=True, env={**self.env, 'TEST_PYTHON': os.environ.get('PYTHON', 'python')}, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('FAIL_RC=7', result.stdout)


if __name__ == '__main__':
    unittest.main()
