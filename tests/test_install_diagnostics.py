"""Installer stages/streams and existing configuration preservation."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_host_policy as base


class InstallDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='owrt-install-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.env={**os.environ,'TMPDIR':self.root.as_posix()}
        self.installer=base.ROOT/'install.sh'
        shell=os.environ.get('OWRT_TEST_BUSYBOX',base.SH)
        self.args=[shell,'ash'] if 'busybox' in shell.lower() else [shell]

    def test_full_local_copy_preserves_existing_uci_on_repeat_install(self):
        config=self.root/'etc/config/owrtremote'
        config.parent.mkdir(parents=True)
        original=b"config remote 'main'\n option router_name 'keep custom name'\n option hub_token 'test-secret'\n"
        config.write_bytes(original)
        env={**self.env,'ROOT':self.root.as_posix()}
        for _ in range(2):
            result=subprocess.run(self.args+[self.installer.as_posix()],env=env,cwd=base.ROOT,
                                  capture_output=True,encoding='utf-8',timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(config.read_bytes(),original)
            self.assertNotIn('test-secret',result.stdout+result.stderr)
            self.assertTrue((self.root/'usr/sbin/owrt-remote').exists())
        self.assertEqual((self.root/'usr/sbin/owrt-remote').read_bytes(),
                         (base.ROOT/'files/usr/sbin/owrt-remote').read_bytes())

    def test_critical_failure_names_stage_command_rc_and_both_streams(self):
        source=self.installer.read_text(encoding='utf-8').split('# Stop the old log writer')[0]
        source+='''\nfail_command() { echo sample-stdout; echo sample-stderr >&2; return 19; }
run_step example-phase fail_command
echo UNREACHABLE
'''
        script=self.root/'driver.sh'; script.write_text(source,encoding='utf-8',newline='\n')
        result=subprocess.run(self.args+[script.as_posix()],capture_output=True,encoding='utf-8',timeout=20,env=self.env)
        self.assertEqual(result.returncode,19,result.stderr)
        for text in ['example-phase','fail_command','rc=19','sample-stdout','sample-stderr']:
            self.assertIn(text,result.stderr)
        self.assertNotIn('UNREACHABLE',result.stdout)

    def test_successful_stderr_is_available_in_diagnostic_mode(self):
        source=self.installer.read_text(encoding='utf-8').split('# Stop the old log writer')[0]
        source+='\nprobe() { echo sample-warning >&2; }; run_step probe-stage probe\n'
        script=self.root/'driver.sh'; script.write_text(source,encoding='utf-8',newline='\n')
        result=subprocess.run(self.args+[script.as_posix()],capture_output=True,encoding='utf-8',timeout=20,
                              env={**self.env,'OWRT_REMOTE_INSTALL_DIAGNOSTIC':'1'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('sample-warning',result.stderr)
        self.assertIn('rc=0',result.stderr)

    def test_failed_file_download_preserves_installed_agent_and_cleans_staging(self):
        target=self.root/'usr/sbin/sample-agent'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'working agent\n')
        source=self.installer.read_text(encoding='utf-8').split('# Stop the old log writer')[0]
        source+='''\nfetch() { echo partial-candidate >"$2"; echo 'HTTP 404' >&2; return 22; }
install_file usr/sbin/sample-agent 0755
echo UNREACHABLE
'''
        script=self.root/'driver.sh'; script.write_text(source,encoding='utf-8',newline='\n')
        result=subprocess.run(self.args+[script.as_posix()],capture_output=True,encoding='utf-8',timeout=20,
                              env={**self.env,'ROOT':self.root.as_posix()})
        self.assertEqual(result.returncode,22,result.stderr)
        self.assertIn('HTTP 404',result.stderr)
        self.assertEqual(target.read_bytes(),b'working agent\n')
        self.assertFalse(list(target.parent.glob('sample-agent.new.*')))
        self.assertNotIn('UNREACHABLE',result.stdout)

    def test_web_services_start_on_first_install_and_reload_when_running(self):
        for name in ['rpcd','uhttpd']:
            helper=self.root/'etc/init.d'/name
            helper.parent.mkdir(parents=True,exist_ok=True)
            helper.write_text('''#!/bin/sh
printf '%s %s\\n' "${0##*/}" "$1" >>"$INSTALL_CALL_LOG"
case "$1" in
    running) [ "$SERVICE_RUNNING" = 1 ] ;;
    start|reload) exit 0 ;;
    *) echo 'unexpected service command' >&2; exit 99 ;;
esac
''',encoding='utf-8',newline='\n')
            helper.chmod(0o755)
        calls=self.root/'service-calls'
        for running,operation in [('0','start'),('1','reload')]:
            calls.write_text('')
            result=subprocess.run(self.args+[self.installer.as_posix()],capture_output=True,encoding='utf-8',timeout=30,
                                  env={**self.env,'ROOT':self.root.as_posix(),
                                       'INSTALL_CALL_LOG':calls.as_posix(),'SERVICE_RUNNING':running})
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(calls.read_text().splitlines(),['rpcd running','rpcd '+operation,
                                                           'uhttpd running','uhttpd '+operation])


if __name__=='__main__': unittest.main()
