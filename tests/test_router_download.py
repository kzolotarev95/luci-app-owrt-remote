"""Download failures, fallback, budgets and preservation; no public network."""
import os
from pathlib import Path
import shlex
import subprocess
import time
import unittest

import test_xray_bootstrap as base


class DownloaderTests(unittest.TestCase):
    config = base.XrayBootstrapTests.config
    shell = base.XrayBootstrapTests.raw_shell

    def setUp(self):
        base.XrayBootstrapTests.setUp(self)
        if os.name == 'nt':
            self.clients += '\nulimit() { [ "$1 $2" = "-f 32768" ]; }\n'
        tools = self.directory / 'tools'
        tools.mkdir()
        native = tools / 'uclient-fetch'
        native.write_text('#!/bin/sh\n' + self.clients + '\nuclient_fetch_mock "$@"\n',
                          encoding='utf-8', newline='\n')
        native.chmod(0o755)
        self.env['PATH'] = tools.as_posix() + os.pathsep + self.env['PATH']

    clients = r'''
command() {
    if [ "$1" = -v ]; then
        case "$2" in curl|wget|uclient-fetch)
            case " $TEST_TOOLS " in *" $2 "*) printf '%s\n' "$2"; return 0 ;; *) return 1 ;; esac ;;
        esac
    fi
    builtin command "$@"
}
curl() {
    printf 'curl\n' >>"$TEST_ROOT/clients"
    local previous arg output
    previous=''
    for arg in "$@"; do [ "$previous" != -o ] || output="$arg"; previous="$arg"; done
    printf partial >"$output"
    [ "${CURL_RC:-6}" = 0 ] || { printf 'could not resolve SECRET_URL\n' >&2; return "${CURL_RC:-6}"; }
    printf payload >"$output"; printf '200 \n'
}
uclient_fetch_mock() {
    printf 'uclient-fetch\n' >>"$TEST_ROOT/clients"
    local previous arg output
    previous=''
    for arg in "$@"; do [ "$previous" != -O ] || output="$arg"; previous="$arg"; done
    printf partial >"$output"
    [ "${NATIVE_RC:-0}" = 0 ] || { printf 'TLS certificate error\n' >&2; return "$NATIVE_RC"; }
    printf payload >"$output"
}
wget() { printf 'wget\n' >>"$TEST_ROOT/clients"; uclient_fetch_mock "$@"; }
'''

    def download(self, extra='', **env):
        return self.shell('fetch_file "https://github.com/test?token=SECRET_TOKEN" "$TEST_ROOT/download"',
                          mocks=self.clients + extra, TEST_TOOLS='curl uclient-fetch wget', **env)

    def test_curl_failure_switches_to_next_client_without_leaking_stderr_or_url(self):
        result = self.download()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.directory / 'download').read_text(), 'payload')
        self.assertEqual((self.directory / 'clients').read_text().splitlines(), ['curl', 'uclient-fetch'])
        self.assertIn('reason=DNS', result.stderr)
        self.assertIn('tool=uclient-fetch', result.stderr)
        self.assertNotIn('SECRET', result.stderr)
        self.assertFalse(list(self.directory.glob('owrt-download.*')))

    def test_native_failure_keeps_original_rc_and_tls_reason(self):
        result = self.shell('fetch_file https://github.com/test "$TEST_ROOT/download"',
                            mocks=self.clients, TEST_TOOLS='uclient-fetch', NATIVE_RC='7')
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn('rc=7; reason=TLS', result.stderr)
        self.assertFalse((self.directory / 'download').exists())

    def test_wget_symlink_to_uclient_is_not_a_second_attempt(self):
        result = self.download(extra=r'''
readlink() { case "$2" in wget|uclient-fetch) printf /bin/uclient-fetch ;; *) printf '%s' "$2" ;; esac; }
''', NATIVE_RC='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.directory / 'clients').read_text().splitlines(), ['curl', 'uclient-fetch'])

    def test_expired_preparation_budget_prevents_all_requests(self):
        result = self.download(XRAY_PREPARE_DEADLINE='1')
        self.assertEqual(result.returncode, 28, result.stderr)
        self.assertFalse((self.directory / 'clients').exists())

    def test_write_error_does_not_retry_another_client(self):
        result = self.download(CURL_RC='23')
        self.assertEqual(result.returncode, 23)
        self.assertIn('reason=write', result.stderr)
        self.assertEqual((self.directory / 'clients').read_text().splitlines(), ['curl'])

    def test_no_head_gate_in_candidate_install(self):
        data = base.XrayBootstrapTests.make_binary(self)
        self.directory.joinpath('release.pkg').write_bytes(data)
        result = base.XrayBootstrapTests.shell(self, 'install_xray_tmp', mocks=r'''
xray_probe_url() { echo forbidden-HEAD >&2; return 22; }
fetch_file() { cp "$TEST_ROOT/release.pkg" "$2"; }
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('forbidden-HEAD', result.stderr)

    def test_network_failure_does_not_search_old_releases_or_report_incompatibility(self):
        data = base.XrayBootstrapTests.make_binary(self)
        self.directory.joinpath('release.pkg').write_bytes(data)
        result = base.XrayBootstrapTests.shell(self, 'install_xray_tmp', mocks=r'''
xray_discover_releases() {
    printf '2\n' >"$2/next-release-page"
    for asset in first second third; do
        printf 'v26.6.27|%s.ipk|https://github.com/%s|%s|%s\n' "$asset" "$asset" "$(xray_digest "$TEST_ROOT/release.pkg")" "$(wc -c <"$TEST_ROOT/release.pkg")"
    done >"$1"
}
fetch_file() { echo "$1" >>"$TEST_ROOT/clients"; return 6; }
''')
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertEqual(len(self.directory.joinpath('clients').read_text().splitlines()), 1)
        self.assertIn('published Xray package found but download failed', result.stderr)
        self.assertNotIn('No compatible', result.stderr)
        self.assertTrue(self.binary.exists())

    def test_prepare_preserves_original_download_return_code(self):
        result = self.shell('prepare_xray', mocks='ensure_xray_available() { return 6; }')
        self.assertEqual(result.returncode, 6, result.stderr)

    def mirror_install(self, corrupt=False):
        good = base.XrayBootstrapTests.make_binary(self, 'working')
        cached = base.XrayBootstrapTests.cache(self, good)
        candidate = base.XrayBootstrapTests.make_binary(self, 'new')
        self.binary.write_bytes(good)
        self.directory.joinpath('release.pkg').write_bytes(candidate)
        result = base.XrayBootstrapTests.shell(self, 'install_xray_tmp', mocks=r'''
fetch_file() {
    printf '%s %s\n' "$1" "$XRAY_FILE_DEADLINE" >>"$TEST_ROOT/clients"
    case "$1" in https://mirror.example/*)
        if [ "$TEST_CORRUPT" = 1 ]; then tr X Y <"$TEST_ROOT/release.pkg" >"$2"; else cp "$TEST_ROOT/release.pkg" "$2"; fi ;;
        *) return 6 ;;
    esac
}
''', XRAY_PACKAGE_MIRROR='https://mirror.example/xray', TEST_CORRUPT='1' if corrupt else '0')
        return result, good, candidate, cached

    def test_opt_in_mirror_uses_same_file_deadline_and_official_digest(self):
        result, good, candidate, _ = self.mirror_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.directory.joinpath('clients').read_text().splitlines()
        self.assertIn('https://mirror.example/xray/v26.6.27/test.ipk', calls[-1])
        self.assertEqual(calls[0].split()[-1], calls[-1].split()[-1])
        self.assertEqual(self.binary.read_bytes(), candidate)

    def test_corrupted_mirror_file_cannot_replace_binary_or_boot_cache(self):
        result, good, candidate, cached = self.mirror_install(corrupt=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SHA256 mismatch', result.stderr)
        self.assertEqual(self.binary.read_bytes(), good)
        self.assertTrue(cached.exists())

    def test_embedded_install_and_agent_downloaders_match_the_single_source(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([os.sys.executable, str(root / 'scripts/embed-router-download.py'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipIf(os.name == 'nt', 'Linux /proc process-tree watchdog integration')
    def test_hung_client_is_killed_and_reaped_before_native_fallback(self):
        started = time.monotonic()
        result = self.shell('XRAY_FILE_DEADLINE=$(( $(download_clock) + 6 )); fetch_file https://github.com/test "$TEST_ROOT/download"',
                            mocks=self.clients + r'''
curl() {
    read -r child rest </proc/self/stat
    printf '%s\n' "$child" >"$TEST_ROOT/hung-pid"
    command sleep 30
}
''', TEST_TOOLS='curl uclient-fetch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, 9)
        pid = int(self.directory.joinpath('hung-pid').read_text())
        self.assertFalse(Path('/proc', str(pid)).exists(), 'HTTP child was not reaped')
        self.assertEqual(self.directory.joinpath('download').read_text(), 'payload')

    @unittest.skipIf(os.name == 'nt', 'Linux ash TERM/EXIT trap integration')
    def test_preparation_deadline_runs_worker_cleanup_and_preserves_working_binary(self):
        good = base.XrayBootstrapTests.make_binary(self)
        result = self.shell('XRAY_PREPARE_DEADLINE=$(( $(download_clock) + 2 )); xray_prepare_timed hung_preparation',
                            mocks=r'''
hung_preparation() (
    trap 'rm -f "$TEST_ROOT/temporary-work"' EXIT
    trap 'exit 1' TERM INT
    touch "$TEST_ROOT/temporary-work"
    command sleep 30
)
''')
        self.assertEqual(result.returncode, 28, result.stderr)
        self.assertFalse(self.directory.joinpath('temporary-work').exists())
        self.assertEqual(self.binary.read_bytes(), good)


if __name__ == '__main__':
    unittest.main()
