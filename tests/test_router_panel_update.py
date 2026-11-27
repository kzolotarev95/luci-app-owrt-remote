"""Exercise the real updater transaction with disposable router files."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIT_BIN = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin'
SH = str(GIT_BIN / 'sh.exe') if os.name == 'nt' else shutil.which('sh')
spec = importlib.util.spec_from_file_location('router_manifest', ROOT / 'scripts/router-update-manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)
COMMIT = 'a' * 40


@unittest.skipUnless(SH, 'POSIX shell required')
class RouterPanelUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owrt-panel-update-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'router'
        self.source = self.base / 'source'
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for name in manifest.FILES:
            data = (ROOT / 'files' / name).read_bytes()
            for folder in (self.root, self.source):
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(int(manifest.FILES[name], 8))
        self.old_agent = '#!/bin/sh\necho "xray_state: running"\n'
        self.service = '#!/bin/sh\ncase "$1" in restart) echo restart >>"$OWRT_PANEL_ROOT/restarts";; running) true;; esac\n'
        self.put('usr/sbin/owrt-remote', self.old_agent, both=True)
        self.put('etc/init.d/owrt-remote', self.service, both=True)
        self.put('usr/share/owrt-remote/panel-version', '125 1\n')
        (self.source / 'usr/share/owrt-remote/panel-version').write_text('126 1\n', newline='\n')
        self.put('www/cgi-bin/owrt-remote', '#!/bin/sh\necho old-panel\n')
        self.protected = {}
        for name in ('etc/config/owrtremote', 'etc/owrt-remote/web.key',
                     'etc/xray/owrt-remote-client.json', 'tmp/owrt-xray/xray',
                     'etc/owrt-remote/xray-cache/xray.gz', 'etc/owrt-remote/control/stopped'):
            self.put(name, 'preserve ' + name + '\n')
            self.protected[name] = (self.root / name).read_bytes()
        self.env = dict(os.environ, OWRT_PANEL_ROOT=self.root.as_posix(),
                        FIXTURE_SOURCE=self.source.as_posix(), FIXTURE_BASE=self.base.as_posix())
        self.env['PATH'] = str(self.bin) + os.pathsep + (str(GIT_BIN) + os.pathsep if os.name == 'nt' else '') + self.env['PATH']
        # jsonfilter itself is exercised in real OpenWrt VM tests. This shim
        # only supplies its JSON parsing contract on the Windows test host.
        parser = self.bin / 'jsonfilter.py'
        parser.write_text('import json,sys\na=sys.argv;v=json.load(open(a[a.index("-i")+1],encoding="utf-8"));e=a[a.index("-e")+1];print(v[e[2:]] if e.startswith("@.") else json.dumps(v))\n')
        python = Path(os.sys.executable).as_posix()
        script = '#!/bin/sh\nexec ' + shlex.quote(python) + ' ' + shlex.quote(parser.as_posix()) + ' "$@"\n'
        (self.bin / 'jsonfilter').write_text(script, newline='\n')
        (self.bin / 'jsonfilter').chmod(0o755)
        self.make_manifest()
        self.before = {name: (self.root / name).read_bytes() for name in manifest.FILES}

    def put(self, name, data, both=False):
        for folder in (self.root, self.source) if both else (self.root,):
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data, encoding='utf-8', newline='\n')
            target.chmod(0o755)

    def make_manifest(self, version=126, release=1):
        lines = ['OWRT_ROUTER_UPDATE 1', f'version {version}', f'release {release}']
        for name, mode in manifest.FILES.items():
            lines.append(hashlib.sha256((self.source / name).read_bytes()).hexdigest() + ' ' + mode + ' ' + name)
        (self.source / 'manifest').write_text('\n'.join(lines) + '\n', newline='\n')

    def run_worker(self, operation='install', commit=COMMIT, mutate_download=''):
        state = self.root / 'tmp/owrt-remote-panel-update'
        work = state / 'work-fixture'
        work.mkdir(parents=True)
        (state / 'lock').mkdir()
        downloader = '''
download_clock() { date +%s; }
remote_download() {
    case "$1" in
        https://api.github.com/repos/kzolotarev95/luci-app-owrt-remote/commits/main)
            printf '{"sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' >"$2" ;;
        https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/router-update.manifest)
            cp "$FIXTURE_SOURCE/manifest" "$2" ;;
        https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/files/*)
            path=${1#*/files/}; cp "$FIXTURE_SOURCE/$path" "$2" ;;
        *) exit 90 ;;
    esac
}
'''
        (work / 'download.sh').write_text(mutate_download or downloader, newline='\n')
        # A running shell reads a private copy, so replacing SELF cannot change
        # the worker halfway through the transaction.
        worker = work / 'worker.sh'
        worker.write_bytes((ROOT / 'files/usr/lib/owrt-remote/panel-update.sh').read_bytes())
        result = subprocess.run([SH, str(worker), 'worker', 'fixture', operation, commit], env=self.env,
                                capture_output=True, encoding='utf-8', timeout=30)
        status = json.loads((state / 'status.json').read_text(encoding='utf-8'))
        for name, data in self.protected.items():
            self.assertEqual((self.root / name).read_bytes(), data, name)
        self.assertFalse((state / 'lock').exists(), result.stderr)
        self.assertFalse(work.exists(), result.stderr)
        self.assertFalse(list(self.root.rglob('*.panel-new')))
        return result, status

    def assert_unchanged(self):
        for name, data in self.before.items():
            self.assertEqual((self.root / name).read_bytes(), data, name)

    def test_available_does_not_modify_files(self):
        result, status = self.run_worker('check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status['status'], 'available')
        self.assertEqual(status['revision'], COMMIT)
        self.assert_unchanged()

    def test_install_all_hashes_and_backup_then_explicit_rollback(self):
        result, status = self.run_worker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status['status'], 'succeeded')
        self.assertEqual(status['installed_version'], 'v126')
        for name in manifest.FILES:
            self.assertEqual((self.root / name).read_bytes(), (self.source / name).read_bytes(), name)
            self.assertEqual((self.root / 'etc/owrt-remote/panel-update-backup' / name).read_bytes(), self.before[name])
        result, status = self.run_worker('rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status['status'], 'succeeded')
        self.assert_unchanged()

    def test_current_build(self):
        for name in manifest.FILES:
            (self.root / name).write_bytes((self.source / name).read_bytes())
        _, status = self.run_worker('check')
        self.assertEqual(status['status'], 'current')

    def test_same_version_fixes_are_available(self):
        self.make_manifest(125)
        _, status = self.run_worker('check')
        self.assertEqual(status['status'], 'available')

    def test_downgrade_is_skipped(self):
        self.make_manifest(124)
        _, status = self.run_worker()
        self.assertEqual(status['status'], 'local_newer')
        self.assert_unchanged()

    def test_changed_main_requires_new_confirmation(self):
        result, status = self.run_worker(commit='b' * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Источник изменился', status['message'])
        self.assert_unchanged()

    def test_no_internet_or_http_error_preserves_installation(self):
        result, status = self.run_worker(mutate_download='download_clock() { date +%s; }\nremote_download() { return 22; }\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(status['status'], 'failed')
        self.assertIn('GitHub', status['message'])
        self.assert_unchanged()

    def test_corrupt_file_checksum_preserves_installation(self):
        (self.source / 'usr/lib/owrt-remote/wan.sh').write_text('corrupt\n')
        result, status = self.run_worker()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Контрольная сумма', status['message'])
        self.assert_unchanged()

    def test_invalid_shell_even_with_matching_checksum(self):
        (self.source / 'usr/lib/owrt-remote/wan.sh').write_text('if then\n')
        self.make_manifest()
        result, status = self.run_worker()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('shell', status['message'])
        self.assert_unchanged()

    def test_unknown_path_and_duplicate_inventory_rejected(self):
        path = self.source / 'manifest'
        text = path.read_text()
        path.write_text(text.replace('usr/sbin/owrt-remote', '../../etc/shadow'))
        result, _ = self.run_worker()
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()
        path.write_text(text + text.splitlines()[3] + '\n')
        result, _ = self.run_worker()
        self.assertNotEqual(result.returncode, 0)
        self.assert_unchanged()

    def test_restart_failure_restores_all_files(self):
        (self.source / 'etc/init.d/owrt-remote').write_text('#!/bin/sh\nexit 1\n')
        self.make_manifest()
        result, status = self.run_worker()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Предыдущая сборка восстановлена', status['message'])
        self.assert_unchanged()

    def test_stopped_service_remains_stopped(self):
        service = '#!/bin/sh\ncase "$1" in running) exit 1;; restart) echo UNEXPECTED >&2; exit 99;; esac\n'
        self.put('etc/init.d/owrt-remote', service, both=True)
        self.make_manifest()
        result, status = self.run_worker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status['status'], 'succeeded')
        self.assertFalse((self.root / 'restarts').exists())

    def test_cgi_authentication_and_csrf(self):
        def cgi(key='secret', method='GET', header='', operation='check'):
            keyfile = self.base / 'web.key'; keyfile.write_text('secret\n')
            env = dict(self.env, KEY_FILE=keyfile.as_posix(), TMPDIR=self.base.as_posix(),
                       REQUEST_METHOD=method, QUERY_STRING=f'key={key}&action=panel_update&operation={operation}',
                       CONTENT_LENGTH='0', HTTP_X_OWRT_PANEL_UPDATE=header,
                       PANEL_UPDATER=(self.root / 'usr/lib/owrt-remote/panel-update.sh').as_posix())
            return subprocess.run([SH, str(ROOT / 'files/www/cgi-bin/owrt-remote')], env=env,
                                  capture_output=True, encoding='utf-8', timeout=10).stdout
        self.assertIn('403 Forbidden', cgi())
        self.assertIn('403 Forbidden', cgi(method='POST'))
        self.assertIn('Доступ', cgi(key='wrong'))
        self.assertIn('"status":"idle"', cgi(operation='status'))
        self.assertIn('400 Bad Request', cgi(method='POST', header='1', operation='rollback'))


if __name__ == '__main__':
    unittest.main()
