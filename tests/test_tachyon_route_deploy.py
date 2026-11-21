"""Hub and Xray must be validated and restored as one local deployment."""
import os
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from test_host_policy import ROOT, SH


@unittest.skipUnless(SH, 'POSIX shell required')
class RouteDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hub = self.root / 'hub.py'
        self.xray = self.root / 'xray.json'
        self.hub.write_text('old hub')
        self.xray.write_text('old xray')
        self.env = {**os.environ, 'PATH': str(Path(SH).parent) + os.pathsep + os.environ['PATH']}
        script = (ROOT / 'vps/deploy-tachyon-route-fix.ps1').read_text(encoding='utf-8')
        self.script = script.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        for old, path in (('backup=/root/owrt-remote-backup-before-tachyon-route-fix-v121', self.root / 'backup'),
                          ('hub=/opt/owrt-remote/owrt-remote-hub.py', self.hub),
                          ('xray_config=/etc/xray/owrt-remote.json', self.xray)):
            self.script = self.script.replace(old, old.split('=', 1)[0] + '=' + shlex.quote(path.as_posix()))
        self.script = self.script.replace('test -x /usr/local/bin/xray', 'true').replace('/usr/local/bin/xray -test', 'test_xray -test')
        self.mocks = r'''
python3() {
    if [ "$1" = -m ]; then return 0; fi
    if [ "$1" = - ] && [ "$#" = 3 ]; then cat >/dev/null; printf 'new xray' >"$3"; return; fi
    if [ "$1" = - ]; then cat >/dev/null; [ "${FAIL_HEALTH:-0}" = 0 ]; return; fi
    printf 'new xray' >"$4"
}
test_xray() { [ "${FAIL_VALIDATION:-0}" = 0 ]; }
systemctl() { [ "${FAIL_RESTART:-0}" = 0 ]; }
'''

    def run_deploy(self, action='Install', **flags):
        stage = self.root / 'stage'
        stage.mkdir()
        (stage / 'hub.py').write_text('new hub')
        script = self.script.replace('stage=/tmp/__UPLOAD_NAME__', 'stage=' + shlex.quote(stage.as_posix()))
        script = script.replace('__UPLOAD_NAME__', 'test').replace('__ACTION__', action)
        result = subprocess.run([SH], input=self.mocks + script, capture_output=True, text=True,
                                env={**self.env, **flags}, timeout=20)
        self.assertFalse(stage.exists())
        return result

    def test_success_and_rollback_restore_both_files(self):
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.hub.read_text(), 'new hub')
        self.assertEqual(self.xray.read_text(), 'new xray')
        result = self.run_deploy('Rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.hub.read_text(), 'old hub')
        self.assertEqual(self.xray.read_text(), 'old xray')

    def test_invalid_xray_never_replaces_either_file(self):
        result = self.run_deploy(FAIL_VALIDATION='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('validate-xray', result.stderr)
        self.assertEqual(self.hub.read_text(), 'old hub')
        self.assertEqual(self.xray.read_text(), 'old xray')
        self.assertFalse((self.root / 'backup').exists())

    def test_failed_restart_restores_current_attempt(self):
        result = self.run_deploy(FAIL_RESTART='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Restoring Hub and Xray', result.stderr)
        self.assertEqual(self.hub.read_text(), 'old hub')
        self.assertEqual(self.xray.read_text(), 'old xray')

    def test_missing_backup_is_read_only(self):
        result = self.run_deploy('Rollback')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.hub.read_text(), 'old hub')
        self.assertEqual(self.xray.read_text(), 'old xray')

    def test_config_patch_preserves_vless_port_clients_and_other_services(self):
        text=(ROOT/'vps/deploy-tachyon-route-fix.ps1').read_text()
        code=text.split("<<'PY'\n",1)[1].split('\nPY',1)[0]
        config={'inbounds':[{'tag':'vless','port':9443,'settings':{'clients':[{'id':'test-secret'}]}},
            {'tag':'tachyon-main','protocol':'tunnel','listen':'127.0.0.1','port':24000,
             'settings':{'allowedNetwork':'tcp','rewriteAddress':'127.0.0.1','rewritePort':1680}}],
            'outbounds':[{'tag':'custom','protocol':'freedom'}], 'log':{'loglevel':'warning'}}
        source=self.root/'source.json'; target=self.root/'candidate.json'
        source.write_text(json.dumps(config))
        result=subprocess.run([sys.executable,'-c',code,str(source),str(target)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        actual=json.loads(target.read_text())
        self.assertEqual(actual['inbounds'][0],config['inbounds'][0])
        self.assertEqual(actual['outbounds'],config['outbounds'])
        settings=actual['inbounds'][1]['settings']
        self.assertEqual(settings['port'],1680)
        self.assertEqual(settings['address'],'127.0.0.1')
        self.assertEqual(settings['network'],'tcp')


if __name__ == '__main__':
    unittest.main()
