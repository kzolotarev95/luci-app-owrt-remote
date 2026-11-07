"""Native package contents, release discovery, and safe target setup."""
import importlib.util
import gzip
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("openwrt_package", ROOT / "scripts/openwrt_package.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
SH = shutil.which("sh") or str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/sh.exe")


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owrt-package-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(os.environ)
        if os.name == "nt":
            self.env["PATH"] = str(Path(SH).parent) + os.pathsep + self.env["PATH"]

    def test_prepared_sdk_package_is_self_contained_and_same_overlay(self):
        directory = p.prepare(self.root / "staging")
        for name, (data, mode) in p.payload().items():
            path = directory / "root" / name
            self.assertEqual(path.read_bytes(), data, name)
            self.assertNotIn(b"\r\n", data, name)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, mode, name)
        self.assertEqual((directory / "Makefile").read_bytes(), (p.RECIPE / "Makefile").read_bytes())
        self.assertFalse((directory / "root/etc/owrt-remote/web.key").exists())

    def test_staging_never_deletes_nonempty_directory(self):
        keep = self.root / "keep"
        keep.write_text("do not delete")
        with self.assertRaises(ValueError):
            p.prepare(self.root)
        self.assertEqual(keep.read_text(), "do not delete")

    def test_local_ipk_is_native_and_contains_valid_payload_and_dependencies(self):
        package = p.pack_ipk(self.root / "test.ipk")
        result = p.verify_package(package)
        self.assertTrue(result["verified"])
        self.assertGreater(result["bytes"], 10000)
        with tarfile.open(package, "r:gz") as outer:
            inner = outer.extractfile("./data.tar.gz").read()
        with tarfile.open(fileobj=io.BytesIO(inner), mode="r:gz") as archive:
            # opkg does not create arbitrary missing parent directories when
            # extracting a file; a package needs explicit directory entries.
            self.assertTrue(archive.getmember("./usr/lib/owrt-remote").isdir())
        content = p.ipk_contents(package)
        self.assertEqual(content["data"]["etc/config/owrtremote"][1:], (0o600, 0, 0))
        for name in p.EXECUTABLES:
            self.assertEqual(content["data"][name][1], 0o755, name)
        self.assertIn(b"package.sh configure", content["control"]["postinst-pkg"][0])

    def test_package_bytes_are_reproducible(self):
        self.assertEqual(p.pack_ipk(self.root / "one.ipk").read_bytes(), p.pack_ipk(self.root / "two.ipk").read_bytes())

    def test_package_rejects_missing_files_or_changed_modes(self):
        files = {name: (data, mode, 0, 0) for name, (data, mode) in p.payload().items()}
        files.pop("usr/sbin/owrt-remote")
        with self.assertRaisesRegex(ValueError, "missing"):
            p.validate_payload(files)
        files["usr/sbin/owrt-remote"] = (b"bad replacement", 0o644, 0, 0)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            p.validate_payload(files)

    def test_source_version_matches_router_titles(self):
        version, _ = p.versions()
        for name in ("www/cgi-bin/owrt-remote", "www/luci-static/resources/view/owrt_remote.js"):
            text = (ROOT / "files" / name).read_text(encoding="utf-8")
            self.assertIn("OpenWrt Remote Hub v" + version, text)

    def test_package_scripts_defer_procd_lifecycle_to_openwrt_for_apk_and_ipk(self):
        script = p.package_hook("postinst")
        self.assertNotIn("configure ]", script)
        self.assertIn("IPKG_INSTROOT", script)
        self.assertNotIn("/etc/init.d/owrt-remote restart", script)
        for script in p.native_wrappers().values():
            result = subprocess.run([SH, "-n"], input=script, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def target_setup(self, existing=False, rpcd_fails=False):
        target = self.root / "target"
        target.mkdir()
        for folder in ("etc/config", "etc/owrt-remote", "etc/init.d", "tmp/luci-modulecache"):
            (target / folder).mkdir(parents=True, exist_ok=True)
        config = target / "etc/config/owrtremote"
        config.write_text("config remote 'main'\n option router_name 'Keep name'\n option hub_token 'test-secret'\n")
        key = target / "etc/owrt-remote/web.key"
        if existing:
            key.write_text("existing-private-key\n")
        rpcd = target / "etc/init.d/rpcd"
        rpcd.write_text("#!/bin/sh\nexit " + ("9" if rpcd_fails else "0") + "\n", newline="\n")
        rpcd.chmod(0o755)
        helper = (p.RECIPE / "root/usr/lib/owrt-remote/package.sh").read_text()
        # Redirect target paths in the harness. The production hook never
        # honors arbitrary ROOT/host variables supplied to a package manager.
        # One pass is essential on Linux: a temporary target itself starts
        # with /tmp/, so sequential replacements would rewrite inserted paths.
        helper = re.sub(r"/(?:etc|tmp)/", lambda match: target.as_posix() + match.group(0), helper)
        result = subprocess.run([SH, "-s", "--", "configure"], input=helper, capture_output=True, text=True, env=self.env)
        return result, key, config

    def test_target_setup_creates_private_key_and_preserves_uci(self):
        result, key, config = self.target_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(key.read_text(), r"^[0-9a-f]{64}\n$")
        self.assertIn("test-secret", config.read_text())
        self.assertNotIn("test-secret", result.stdout + result.stderr)
        self.assertNotIn(key.read_text().strip(), result.stdout + result.stderr)

    def test_target_setup_keeps_existing_panel_key(self):
        result, key, _ = self.target_setup(existing=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(key.read_text(), "existing-private-key\n")

    def test_target_setup_reports_rpcd_failure(self):
        result, _, _ = self.target_setup(rpcd_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rpcd reload failed", result.stderr)

    def test_sdk_discovery_handles_xz_and_zstd_without_guessed_filename(self):
        for ext in ("xz", "zst"):
            name = f"openwrt-sdk-24.10.8-x86-64_gcc-13.3.0_musl.Linux-x86_64.tar.{ext}"
            self.assertEqual(p.sdk_name(f'<a href="{name}">SDK</a>'), name)
        with self.assertRaises(ValueError):
            p.sdk_name('<a href="../../malicious.tar.zst">wrong</a>')

    def test_checksum_requires_exact_sdk_and_valid_digest(self):
        digest = "a" * 64
        self.assertEqual(p.checksum(digest + " *sdk.tar.xz\n", "sdk.tar.xz"), digest)
        for text in ("bad *sdk.tar.xz\n", digest + " *other-sdk.tar.xz\n", ""):
            with self.assertRaises(ValueError):
                p.checksum(text, "sdk.tar.xz")

    def test_latest_release_is_numeric_and_ignores_prereleases(self):
        html = ''.join(f'<a href="{v}/">link</a>' for v in ("24.10.9", "24.10.10", "24.10.11-rc1", "25.12.6"))
        self.assertEqual(p.latest_release("24.10", html), "24.10.10")

    def test_matrix_has_all_requested_architectures_and_one_publication_per_series(self):
        builds = p.matrix(baseline=True)["include"]
        self.assertEqual(len(builds), 30)
        self.assertEqual(sum(bool(x.get("publish")) for x in builds), 3)
        self.assertEqual({x["format"] for x in builds}, {"ipk", "apk"})
        self.assertTrue({"mips64", "mipsel_24kc", "aarch64_generic", "x86_64"} <= {x["id"] for x in builds})

    def test_corrupt_sdk_download_never_replaces_existing_cache(self):
        destination = self.root / "sdk.tar.xz"
        destination.write_bytes(b"previous download")
        with mock.patch.object(p.urllib.request, "urlopen", side_effect=lambda *a, **k: io.BytesIO(b"corrupt")), mock.patch.object(p.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                p.download("https://downloads.openwrt.org/example", destination, "a" * 64)
        self.assertEqual(destination.read_bytes(), b"previous download")
        self.assertFalse(destination.with_name(destination.name + ".partial").exists())

    def test_verified_sdk_cache_needs_no_network(self):
        destination = self.root / "sdk.tar.xz"
        destination.write_bytes(b"verified SDK archive")
        with mock.patch.object(p.urllib.request, "urlopen") as network:
            p.download("https://downloads.openwrt.org/example", destination, p.sha256(destination))
        network.assert_not_called()

    def test_invalid_sdk_cache_is_replaced_only_by_verified_download(self):
        destination = self.root / "sdk.tar.xz"
        destination.write_bytes(b"invalid cache")
        expected = self.root / "expected.tar.xz"
        expected.write_bytes(b"verified SDK archive")
        with mock.patch.object(p.urllib.request, "urlopen", return_value=io.BytesIO(expected.read_bytes())) as network:
            p.download("https://downloads.openwrt.org/example", destination, p.sha256(expected))
        network.assert_called_once()
        self.assertEqual(destination.read_bytes(), expected.read_bytes())
        self.assertFalse(destination.with_name(destination.name + ".partial").exists())

    def test_openwrt_disk_image_accepts_fwtool_footer_and_rejects_truncation(self):
        source, destination = self.root / "image.gz", self.root / "image.raw"
        body = b"OpenWrt disk sectors" * 10000
        source.write_bytes(gzip.compress(body) + b'# fwtool image metadata')
        p.unpack_image(source, destination)
        self.assertEqual(destination.read_bytes(), body)
        source.write_bytes(gzip.compress(body)[:-5])
        with self.assertRaisesRegex(ValueError, "truncated"):
            p.unpack_image(source, destination)


if __name__ == "__main__":
    unittest.main()
