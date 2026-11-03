import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("owrt_remote_hub_global_design", ROOT / "vps" / "owrt-remote-hub.py")
hub = importlib.util.module_from_spec(SPEC)
STATE = tempfile.TemporaryDirectory(prefix="owrt-global-design-")
sys.modules[SPEC.name] = hub
with mock.patch.dict(os.environ, {"OWRT_REMOTE_STATE_DIR": STATE.name}):
    SPEC.loader.exec_module(hub)


def preview_pages():
    router = {"id": "main", "name": "Test router", "permissions": {"manage": True}}
    with mock.patch.object(hub, "web_push_backend_status", return_value={"ready": False}):
        pages = {
            "/": hub.dashboard_html([], "root", auth_bootstrap={"is_owner": True}),
            "/login": hub.login_html(),
            "/vps-terminal/": hub.ssh_terminal_html_v2(hub.vps_terminal_row(), "test"),
            "/router-updates/": hub.router_updates_html("test"),
            "/router/main/logs": hub.router_logs_html(router),
            "/router/main/notes": hub.router_notes_html(router),
            "/ssh/main/": hub.ssh_terminal_html_v2(hub.vps_terminal_row(), "test"),
        }
    return {path: hub.inject_hub_appearance(path, page.encode("utf-8"), "text/html").decode("utf-8")
            for path, page in pages.items()}


class GlobalHubDesignTests(unittest.TestCase):
    def test_generated_pages_receive_the_shared_head(self):
        pages = {
            "login": hub.login_html(),
            "terminal": hub.ssh_terminal_html_v2(hub.vps_terminal_row(), "token"),
            "updates": hub.router_updates_html("csrf"),
        }
        for path, page in pages.items():
            result = hub.inject_hub_appearance("/" + path, page.encode("utf-8"), "text/html; charset=utf-8")
            text = result.decode("utf-8")
            self.assertEqual(text.count("owrt-hub-design-head"), 1, path)
            self.assertIn("DESIGN_KEY", text, path)
            for mode in ("snow", "rain", "cosmos", "embers", "orbit", "prism", "pulse", "laser"):
                self.assertIn("'" + mode + "'", text, path)
            self.assertIn("#routerSearchToggle", text, path)

    def test_existing_dashboard_and_luci_are_not_double_injected(self):
        dashboard = hub.dashboard_html([], "root")
        self.assertEqual(dashboard.count("owrt-hub-design-head"), 1)
        luci = b"<html><head><title>LuCI</title></head><body>router</body></html>"
        self.assertEqual(hub.inject_hub_appearance("/access/main/", luci, "text/html"), luci)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--export-preview":
        pathlib.Path(sys.argv[2]).write_text(json.dumps(preview_pages(), ensure_ascii=False), encoding="utf-8")
    else:
        unittest.main()
