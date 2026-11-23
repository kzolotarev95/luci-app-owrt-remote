"""VPS metrics, shared sampling and authenticated HTTP regression checks."""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
from unittest import mock

SOURCE = Path(os.environ.get("OWRT_VPS_WIDGETS_HUB_SOURCE", Path(__file__).resolve().parents[1] / "vps/owrt-remote-hub.py"))
STATE = tempfile.TemporaryDirectory(prefix="owrt-vps-widgets-")
spec = importlib.util.spec_from_file_location("vps_widgets_hub", SOURCE)
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {"OWRT_REMOTE_STATE_DIR": STATE.name}):
    spec.loader.exec_module(hub)


class ResourceSampleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=STATE.name)
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name)
        self.proc.joinpath("stat").write_text("cpu 100 20 30 500 50 10 10 10 40 20\n")
        self.proc.joinpath("meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 600 kB\nMemFree: 100 kB\nCached: 500 kB\n")
        self.monitor = hub.VpsResourceMonitor(self.proc)

    def test_cpu_uses_interval_delta_without_double_counting_guest_ticks(self):
        # 100 total ticks, 70 idle+iowait ticks; guest counters are duplicated.
        self.proc.joinpath("stat").write_text("cpu 110 20 40 550 70 15 15 10 45 20\n")
        self.assertEqual(self.monitor.cpu_usage()["percent"], 30.0)
        self.assertIsNone(self.monitor.cpu_usage()["percent"], "zero interval must not invent a CPU reading")
        self.proc.joinpath("stat").write_text("cpu 1 0 0 1\n")
        self.assertIsNone(self.monitor.cpu_usage()["percent"], "reset counters must not give negative load")

    def test_memory_counts_available_cache_as_available_ram(self):
        memory = self.monitor.memory_usage()
        self.assertEqual(memory["percent"], 40.0)
        self.assertEqual(memory["used_bytes"], 400 * 1024)
        self.assertEqual(memory["available_bytes"], 600 * 1024)

    def test_memory_fallback_and_bounds(self):
        self.proc.joinpath("meminfo").write_text("MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 200 kB\nSReclaimable: 100 kB\nShmem: 50 kB\n")
        self.assertEqual(self.monitor.memory_usage()["percent"], 60.0)
        self.proc.joinpath("meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 2000 kB\n")
        self.assertEqual(self.monitor.memory_usage()["percent"], 0.0)

    def test_disk_reports_root_filesystem_used_free_and_total(self):
        with mock.patch.object(hub.shutil, "disk_usage", return_value=SimpleNamespace(total=1000, used=250, free=750)) as disk:
            metric = self.monitor.disk_usage()
        disk.assert_called_once_with("/")
        self.assertEqual(metric, {"percent": 25.0, "used_bytes": 250, "total_bytes": 1000, "free_bytes": 750, "path": "/"})

    def test_shared_cache_samples_once_for_concurrent_viewers_then_refreshes(self):
        with mock.patch.object(hub.time, "monotonic", return_value=10) as clock, mock.patch.object(self.monitor, "cpu_usage", return_value={"percent": 30, "cores": 2}) as cpu:
            results = []
            threads = [threading.Thread(target=lambda: results.append(self.monitor.snapshot())) for _ in range(8)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(timeout=5)
            self.assertEqual(len(results), 8)
            self.assertEqual(cpu.call_count, 1)
            clock.return_value = 10.99
            self.monitor.snapshot()
            self.assertEqual(cpu.call_count, 1)
            clock.return_value = 11
            self.monitor.snapshot()
            self.assertEqual(cpu.call_count, 2)

    def test_unavailable_proc_files_do_not_break_disk_or_response(self):
        self.proc.joinpath("stat").write_text("invalid stat\n")
        self.proc.joinpath("meminfo").write_text("MemTotal: invalid\n")
        with mock.patch.object(hub.shutil, "disk_usage", return_value=SimpleNamespace(total=1000, used=250, free=750)):
            result = self.monitor.snapshot()
        self.assertIsNone(result["cpu"]["percent"])
        self.assertIsNone(result["memory"])
        self.assertEqual(result["disk"]["percent"], 25)
        with mock.patch.object(hub.shutil, "disk_usage", side_effect=OSError("unavailable")):
            self.assertIsNone(self.monitor.disk_usage())


class QuietHandler(hub.Handler):
    def log_message(self, *args):
        pass


class ResourceHttpTests(unittest.TestCase):
    def test_dashboard_contains_metrics_before_browser_fetch(self):
        app = hub.App(Path(STATE.name) / "dashboard.db", "test-session", "test-agent", "")
        server = hub.HubHTTPServer(("127.0.0.1", 0), QuietHandler)
        server.app = app
        server.is_tls = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        sample = {"cpu": {"percent": 12.5}, "memory": {"percent": 40},
                  "disk": {"percent": 60}, "sampled_at": 123}
        try:
            with mock.patch.object(app.vps_resources, "snapshot", return_value=sample) as snapshot, \
                    mock.patch.object(hub.Handler, "legacy_admin_ok", return_value=False), \
                    mock.patch.object(hub.Handler, "current_hub_session", return_value={"username": "root"}), \
                    mock.patch.object(hub.Handler, "current_account_profile", return_value={"username": "root", "is_owner": True}), \
                    mock.patch.object(hub, "web_push_backend_status", return_value={"ready": False}):
                request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/",
                                                 headers={"Cookie": "owrt_remote_session=test-session"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    page = response.read().decode("utf-8")
                snapshot.assert_called_once_with()
            values = re.findall(r'<strong data-vps-resource-value>([^<]+)</strong>', page)
            self.assertEqual(values, ["12,5%", "40%", "60%"], "one responsive resource panel has values before the browser fetch")
            self.assertIn('aria-label="ЦП: 12,5%"', page)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_unavailable_readings_are_not_invented(self):
        page = hub.vps_resource_widgets_html(resources={"cpu": {"percent": None},
                                                        "memory": {"percent": float("nan")},
                                                        "disk": {"percent": 0}})
        self.assertEqual(re.findall(r'<strong data-vps-resource-value>([^<]+)</strong>', page), ["—", "—", "0%"])

    def test_metrics_require_hub_login_and_never_open_router_database(self):
        app = hub.App(Path(STATE.name) / "unused.db", "test-session", "test-agent", "")
        server = hub.HubHTTPServer(("127.0.0.1", 0), QuietHandler)
        server.app = app
        server.is_tls = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/api/vps/resources"
        try:
            with mock.patch.object(app, "conn", side_effect=AssertionError("metrics must not access SQLite")):
                for headers in ({}, {"Authorization": "Bearer test-agent"}):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5)
                    with error.exception as response:
                        self.assertEqual(response.code, 401)
                        self.assertFalse(json.load(response)["ok"])
                request = urllib.request.Request(url, headers={"Cookie": "owrt_remote_session=test-session"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    data = json.load(response)
                self.assertTrue(data["ok"])
                self.assertEqual(set(data), {"ok", "cpu", "memory", "disk", "sampled_at"})
                self.assertGreater(data["disk"]["total_bytes"], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def export_preview(output):
    output.mkdir(parents=True, exist_ok=True)
    with mock.patch.object(hub, "web_push_backend_status", return_value={"ready": False}):
        page = hub.dashboard_html([], "root", auth_bootstrap={"is_owner": True, "can_add_router": True})
    output.joinpath("dashboard.html").write_text(page, encoding="utf-8")
    output.joinpath("dashboard.js").write_text("\n".join(re.findall(r"<script>(.*?)</script>", page, re.S)), encoding="utf-8")
    print(f"Exported preview: {output}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--export-preview":
        export_preview(Path(sys.argv[2]))
    else:
        unittest.main()
