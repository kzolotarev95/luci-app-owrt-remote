"""Release discovery/ABI/checksum regression tests using BusyBox-compatible shell.

No network or router credentials. Transport/cache tests are in bootstrap suite.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import unittest

import test_xray_bootstrap as bootstrap
import test_host_policy as base


class XrayReleaseSelectionTests(unittest.TestCase):
    setUp = bootstrap.XrayBootstrapTests.setUp
    config = base.RouterShellTests.config
    shell = bootstrap.XrayBootstrapTests.raw_shell

    def setUp(self):
        bootstrap.XrayBootstrapTests.setUp(self)
        release = self.directory / 'openwrt_release'
        release.write_text("DISTRIB_RELEASE='25.12.2'\nDISTRIB_ARCH='mipsel_24kc'\n")
        self.env['OWRT_REMOTE_RELEASE_FILE'] = release.as_posix()
        # JSON filter double supports the exact documented jsonfilter paths.
        self.query = self.directory / 'query.py'
        self.query.write_text('''import json,re,sys
sys.stdout.reconfigure(newline='\\n')
data=json.load(open(sys.argv[1],encoding='utf-8-sig'))
parts=re.findall(r'\\[(\\d+|\\*)\\]|\\.([A-Za-z_]+)',sys.argv[2])
values=[data]
for idx,key in parts:
 out=[]
 for v in values:
  try:
   if key: out.append(v[key])
   elif idx=='*': out.extend(v)
   else: out.append(v[int(idx)])
  except (KeyError,IndexError,TypeError): pass
 values=out
for v in values:
 print(json.dumps(v,separators=(',',':')) if not isinstance(v,str) else v)
sys.exit(0 if values else 1)
''')
        self.fixture = self.directory / 'api.json'
        self.fixture.write_text('[]')
        self.common = r'''
apk() {
 case "$1" in
  --print-arch) echo "${TEST_ARCH:-mipsel}" ;;
  adbdump) cat "$TEST_ROOT/metadata.json" ;;
  add) printf '%s\n' "$*" >>"$TEST_ROOT/dependency-checks"; return "${TEST_DEP_RC:-0}" ;;
  *) return 1 ;;
 esac
}
opkg() { echo forbidden-opkg >&2; return 99; }
jsonfilter() { "$TEST_PYTHON" "$TEST_ROOT/query.py" "$2" "$4"; }
fetch_file() {
 echo "$1" >>"$TEST_ROOT/fetched"
 [ "${TEST_FETCH_RC:-0}" = 0 ] || return "$TEST_FETCH_RC"
 cp "$TEST_ROOT/api.json" "$2"
}
'''
        self.directory.joinpath('metadata.json').write_text(json.dumps({'info': {'name':'openwrt-xray','arch':'mipsel'}}))

    def run_case(self, command, mocks='', **env):
        return self.shell(command, mocks=self.common + mocks, **env)

    def asset(self, tag='v26.6.22', arch='mipsel_24kc', digest=None):
        name=f'openwrt-xray-{tag[1:]}-r1-{arch}.apk'
        return {'name':name,'state':'uploaded','size':123,
                'digest':'sha256:'+(digest or 'a'*64),
                'browser_download_url':'https://github.com/yichya/openwrt-xray/releases/download/'+tag+'/'+name}

    def release(self, tag='v26.6.22', assets=None, **kw):
        return dict(tag_name=tag,draft=False,prerelease=False,assets=assets or [self.asset(tag)],**kw)

    def manifest(self, releases):
        self.fixture.write_text(json.dumps(releases),encoding='utf-8')

    def discover(self, **env):
        return self.run_case('xray_discover_releases "$TEST_ROOT/candidates" "$TEST_ROOT"; rc=$?; cat "$TEST_ROOT/candidates"; exit "$rc"',**env)

    def test_apk_generic_abi_refined_from_openwrt_and_never_calls_opkg(self):
        result=self.run_case('xray_arch; xray_package_format')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.splitlines(),['mipsel_24kc','apk'])
        self.assertNotIn('forbidden',result.stderr)

    def test_precise_apk_architecture_remains_authoritative(self):
        result=self.run_case('xray_arch',TEST_ARCH='aarch64_cortex-a53')
        self.assertEqual(result.stdout.strip(),'aarch64_cortex-a53')

    def test_selects_published_asset_with_checksum_not_constructed_name(self):
        item=self.asset(); item['name']=item['name'].replace('-r1-','-r7-')
        item['browser_download_url']=item['browser_download_url'].replace('-r1-','-r7-')
        self.manifest([self.release(assets=[item])])
        result=self.discover()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('-r7-mipsel_24kc.apk',result.stdout)
        self.assertIn('a'*64,result.stdout)
        self.assertNotIn('-mipsel.apk',result.stdout)

    def test_future_plain_vless_incompatible_release_is_skipped(self):
        self.manifest([self.release('v26.10.31'),self.release('v26.6.22')])
        result=self.discover()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('v26.6.22',result.stdout)
        self.assertNotIn('v26.10.31',result.stdout)

    def test_api_uses_json_even_when_minified_or_reordered(self):
        release=self.release()
        release=dict(reversed(list(release.items())))
        self.manifest([release])
        result=self.discover()
        self.assertEqual(result.returncode,0,result.stderr)

    def test_other_cpu_draft_prerelease_and_missing_digest_rejected(self):
        for changes in ({'draft':True},{'prerelease':True},
                        {'assets':[self.asset(arch='mipsel_74kc')]},
                        {'assets':[dict(self.asset(),digest=None)]}):
            with self.subTest(changes=changes):
                r=self.release(); r.update(changes); self.manifest([r])
                result=self.discover()
                self.assertNotEqual(result.returncode,0)
                self.assertIn('No compatible Xray package found',result.stderr)

    def test_github_api_failure_is_explicit(self):
        result=self.discover(TEST_FETCH_RC='22')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('GitHub API unavailable',result.stderr)
        self.assertFalse(self.binary.exists())

    def test_invalid_json_and_api_error_object_are_not_accepted(self):
        for data in ('{invalid','{"message":"API rate limit exceeded"}'):
            self.fixture.write_text(data)
            result=self.discover()
            self.assertNotEqual(result.returncode,0)

    def test_apk_metadata_requires_target_abi_and_dependency_solver(self):
        result=self.run_case('xray_check_package_metadata dummy "$TEST_ROOT" file.apk')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--simulate --no-network --allow-untrusted',self.directory.joinpath('dependency-checks').read_text())
        self.assertNotIn('forbidden-opkg',result.stderr)

    def test_matching_filename_does_not_override_metadata_abi(self):
        self.directory.joinpath('metadata.json').write_text(json.dumps({'info':{'name':'openwrt-xray','arch':'aarch64'}}))
        result=self.run_case('xray_check_package_metadata dummy "$TEST_ROOT" file.apk')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('incompatible APK ABI',result.stderr)

    def test_dependency_failure_does_not_install_downloaded_package(self):
        result=self.run_case('xray_check_package_metadata dummy "$TEST_ROOT" file.apk',TEST_DEP_RC='7')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('dependencies incompatible',result.stderr)

    def test_url_outside_official_release_is_rejected(self):
        asset=self.asset(); asset['browser_download_url']='https://evil.example/file.apk'
        self.manifest([self.release(assets=[asset])])
        result=self.discover()
        self.assertNotEqual(result.returncode,0)

    def test_second_api_page_is_used_when_first_has_only_incompatible_cores(self):
        self.manifest([self.release('v26.10.'+str(day)) for day in range(1,21)])
        self.directory.joinpath('page2.json').write_text(json.dumps([self.release()]))
        result=self.run_case('xray_discover_releases "$TEST_ROOT/candidates" "$TEST_ROOT"; rc=$?; cat "$TEST_ROOT/candidates"; exit "$rc"', mocks=r'''
fetch_file() {
 echo "$1" >>"$TEST_ROOT/fetched"
 case "$1" in *page=2) cp "$TEST_ROOT/page2.json" "$2" ;; *) cp "$TEST_ROOT/api.json" "$2" ;; esac
}
''')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('mipsel_24kc.apk',result.stdout)
        self.assertIn('page=2',self.directory.joinpath('fetched').read_text())

    def test_generic_cpu_without_openwrt_target_does_not_guess_24kc(self):
        Path(self.env['OWRT_REMOTE_RELEASE_FILE']).write_text("DISTRIB_RELEASE='25.12.2'\n")
        self.manifest([self.release()])
        result=self.discover()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('No compatible Xray package found',result.stderr)

    def test_older_page_is_bookmarked_without_fetching_it_before_validation(self):
        self.manifest([self.release()]+[self.release('v26.10.'+str(i)) for i in range(1,20)])
        result=self.discover()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.directory.joinpath('next-release-page').read_text().strip(),'2')
        self.assertEqual(len(self.directory.joinpath('fetched').read_text().splitlines()),1)


if __name__=='__main__': unittest.main()
