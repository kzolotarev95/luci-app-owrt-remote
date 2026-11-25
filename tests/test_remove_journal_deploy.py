"""Run the router retirement deployer only against disposable files."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SH = 'C:/Program Files/Git/usr/bin/sh.exe' if os.name == 'nt' else '/bin/sh'
FILES = {
    'agent':'usr/sbin/owrt-remote', 'init':'etc/init.d/owrt-remote',
    'cgi':'www/cgi-bin/owrt-remote', 'hotplug':'etc/hotplug.d/iface/99-owrt-remote',
    'control':'usr/lib/owrt-remote/control.sh', 'retire':'usr/lib/owrt-remote/retire-logs.sh',
    'effects':'www/luci-static/resources/owrt_remote_effects.js',
    'tachyon':'usr/lib/owrt-remote/tachyon.sh',
    'luci':'www/luci-static/resources/view/owrt_remote.js',
    'old-helper':'usr/lib/owrt-remote/logs.sh', 'old-init':'etc/init.d/owrt-remote-logs',
}


class JournalDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='retire-deploy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup = self.root/'backup'
        self.state = self.root/'running'
        self.state.touch()
        self.service = f'''#!/bin/sh
case "$1" in
 running) test -f {shlex.quote(self.state.as_posix())} ;;
 start) touch {shlex.quote(self.state.as_posix())} ;;
 stop) rm -f {shlex.quote(self.state.as_posix())} ;;
 *) exit 0 ;;
esac
'''.encode()
        self.initial = {}
        for name, relative in FILES.items():
            path = self.root/relative
            path.parent.mkdir(parents=True,exist_ok=True)
            if name == 'retire':
                self.initial[name] = None
                continue
            data = self.service if name == 'init' else b'#!/bin/sh\n# previous code\nexit 0\n'
            path.write_bytes(data);path.chmod(0o755)
            self.initial[name] = data
        self.config = self.root/'etc/config/owrtremote'
        self.config.parent.mkdir(parents=True)
        self.config.write_bytes(b'Keep UCI, tokens and router name')
        source = (ROOT/'vps/deploy-remove-global-logs.ps1').read_text(encoding='utf-8')
        self.script = source.split("$RemoteScript = @'\n",1)[1].split("\n'@",1)[0]
        mappings = {'/'+relative:(self.root/relative).as_posix() for relative in FILES.values()}
        mappings.update({'/root/owrt-remote-backup-before-remove-global-logs-v124':self.backup.as_posix(),
                         '/tmp/owrt-remote-retire-journal.lock':(self.root/'lock').as_posix()})
        pattern = '|'.join(re.escape(p) for p in sorted(mappings,key=len,reverse=True))
        self.script = re.sub(pattern,lambda m:mappings[m[0]],self.script)
        self.env = {**os.environ,'PATH':str(Path(SH).parent)+os.pathsep+os.environ.get('PATH',''),
            'IPKG_INSTROOT':('/'+self.root.drive[0].lower()+self.root.as_posix()[2:] if os.name=='nt' else self.root.as_posix())}

    def deploy(self, action='Install', fail=False, bad=False):
        stage = self.root/'stage'
        stage.mkdir()
        if action == 'Install':
            for name,relative in FILES.items():
                if name.startswith('old-'):continue
                data = (ROOT/'files'/relative).read_bytes()
                if name == 'init':data = self.service
                if bad and name == 'cgi':data = b'if true; then\n'
                (stage/name).write_bytes(data)
        script = self.script.replace('stage=/tmp/__UPLOAD_NAME__','stage='+shlex.quote(stage.as_posix()))
        script = script.replace('__UPLOAD_NAME__','sandbox').replace('__ACTION__',action)
        if fail:script=script.replace('step=verify','step=verify\nfalse')
        return subprocess.run([SH],input=script,env=self.env,text=True,capture_output=True,timeout=25)

    def assert_original(self):
        for name,data in self.initial.items():
            path=self.root/FILES[name]
            if data is None:self.assertFalse(path.exists(),name)
            else:self.assertEqual(path.read_bytes(),data,name)
        self.assertEqual(self.config.read_bytes(),b'Keep UCI, tokens and router name')
        self.assertTrue(self.state.exists())

    def test_install_and_code_rollback_preserve_running_agent_and_configuration(self):
        result=self.deploy()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse((self.root/FILES['old-helper']).exists())
        self.assertFalse((self.root/FILES['old-init']).exists())
        self.assertTrue(self.state.exists())
        result=self.deploy('Rollback')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assert_original()

    def test_failed_verification_restores_previous_code_and_running_service(self):
        result=self.deploy(fail=True)
        self.assertNotEqual(result.returncode,0)
        self.assert_original()

    def test_invalid_candidate_cannot_stop_service_or_change_files(self):
        result=self.deploy(bad=True)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.backup.exists())
        self.assert_original()


if __name__ == '__main__':
    unittest.main()
