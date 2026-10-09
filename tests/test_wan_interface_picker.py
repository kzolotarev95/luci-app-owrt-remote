"""Router uplink picker regressions using real CGI requests and mocked netifd/UCI."""
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
SH = shutil.which("sh")
GIT_BIN = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin"
if os.name == "nt" and (GIT_BIN / "sh.exe").exists():
    SH = str(GIT_BIN / "sh.exe")


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.options = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name"):
            if attrs["name"] == "wan_interface" and attrs.get("type") == "checkbox":
                selected = "checked" in attrs
                self.options.append((attrs.get("value", ""), selected))
                if selected:
                    self.fields.setdefault("wan_interface", []).append(attrs.get("value", ""))
                return
            if attrs.get("type") != "checkbox" or "checked" in attrs:
                self.fields[attrs["name"]] = attrs.get("value", "")


MOCKS = r'''
uci() {
    [ "$1" != -q ] || shift
    case "$1" in
        show) cat "$TEST_ROOT/network" ;;
        get)
            [ "$2" != owrtremote.main ] || { printf remote; return; }
            awk -F= -v key="$2" '$1==key { sub(/^[^=]*=/, ""); print; found=1; exit } END { if (!found) exit 1 }' "$TEST_ROOT/config"
            ;;
        set)
            key="${2%%=*}"; value="${2#*=}"
            awk -F= -v key="$key" '$1!=key' "$TEST_ROOT/config" >"$TEST_ROOT/config.new"
            printf '%s=%s\n' "$key" "$value" >>"$TEST_ROOT/config.new"
            mv "$TEST_ROOT/config.new" "$TEST_ROOT/config"
            ;;
        commit) printf '%s\n' "$*" >>"$TEST_ROOT/commits" ;;
    esac
}
ubus() {
    [ "$*" = 'call network.interface dump' ] || return 1
    cat "$TEST_ROOT/runtime"
}
jsonfilter() {
    [ "$*" = '-e @.interface[*].interface' ] || return 1
    "$TEST_PYTHON" -c 'import json, sys; sys.stdout.buffer.write("\n".join(i["interface"] for i in json.load(sys.stdin).get("interface", [])).encode())'
}
service_enabled() { return 0; }
xray_running() { return 0; }
render_page() {
    if [ "${TEST_FULL_PAGE:-0}" = 1 ]; then render_full_page; return; fi
    printf '<form><input name="key" value="test-web-key"><input type="checkbox" name="wan_direct" value="1" checked>'
    field router_name 'Название'
    field vps_host 'VPS host'
    wan_interface_field
    printf '</form><p>%s</p>' "$RESULT_TITLE"
}
'''


@unittest.skipUnless(SH, "A POSIX shell is required")
class WanInterfacePickerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owrt-wan-picker-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.directory.joinpath("key").write_text("test-web-key", encoding="utf-8")
        self.directory.joinpath("config").write_text(
            "owrtremote.main.wan_interface=wan\nowrtremote.main.wan_direct=1\n"
            "owrtremote.main.vps_host=192.0.2.25\nowrtremote.main.vps_host_mode=manual\n"
            "owrtremote.main.router_name=Test router\n", encoding="utf-8")
        self.network(["lan", "wan", "wwan", "modem4g"], ["lan", "wan", "wwan"])
        source = (ROOT / "files/www/cgi-bin/owrt-remote").read_text(encoding="utf-8")
        self.cgi = self.directory / "cgi"
        source = source.replace('render_page() {', 'render_full_page() {', 1)
        self.cgi.write_text(source.replace("\nread_request\n", MOCKS + "\nread_request\n", 1), encoding="utf-8", newline="\n")
        self.env = {**os.environ, "TMPDIR": self.directory.as_posix(), "TEST_ROOT": self.directory.as_posix(),
                    "TEST_PYTHON": Path(sys.executable).as_posix(), "KEY_FILE": (self.directory / "key").as_posix(),
                    "AGENT": "true"}
        if os.name == "nt":
            self.env["PATH"] = str(GIT_BIN) + os.pathsep + self.env["PATH"]

    def network(self, configured, runtime):
        self.directory.joinpath("network").write_text(
            "network.globals=globals\n" + "".join(f"network.{name}=interface\nnetwork.{name}.proto='dhcp'\n" for name in configured), encoding="utf-8")
        self.directory.joinpath("runtime").write_text(json.dumps({"interface": [{"interface": name} for name in runtime]}), encoding="utf-8")

    def select(self, value):
        path = self.directory / "config"
        rows = [row for row in path.read_text().splitlines() if not row.startswith("owrtremote.main.wan_interface=")]
        path.write_text("\n".join(rows) + f"\nowrtremote.main.wan_interface={value}\n", encoding="utf-8")

    def request(self, data=None, key="test-web-key"):
        body = urlencode(data or {}, doseq=True).encode("ascii")
        result = subprocess.run([SH, self.cgi.as_posix()], input=body, capture_output=True,
                                env={**self.env, "QUERY_STRING": urlencode({"key": key}),
                                     "REQUEST_METHOD": "POST" if data else "GET", "CONTENT_LENGTH": str(len(body)),
                                     "CONTENT_TYPE": "application/x-www-form-urlencoded"}, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        page = result.stdout.decode("utf-8")
        parser = FormParser()
        parser.feed(page)
        return page, parser

    def test_all_configured_and_runtime_interfaces_appear_once_in_real_page(self):
        self.env["TEST_FULL_PAGE"] = "1"
        self.network(["lan", "wan", "wwan", "modem4g"], ["wan", "wwan", "lte"])
        page, form = self.request()
        self.assertEqual(form.options, [("wan", True), ("lan", False), ("lte", False), ("modem4g", False), ("wwan", False)])
        self.assertIn('<details class="wanPicker" data-wan-picker>', page)
        self.assertNotIn('<select name="wan_interface"', page)

    def test_custom_saved_uplink_is_selected_without_reset(self):
        for name in ("wwan", "modem4g"):
            with self.subTest(name=name):
                self.select(name)
                _, form = self.request()
                self.assertEqual(form.fields["wan_interface"], [name])
                self.assertEqual(sum(selected for _, selected in form.options), 1)

    def test_unavailable_saved_interface_survives_list_failure(self):
        self.select("internet2")
        self.network([], [])
        page, form = self.request()
        self.assertEqual(form.options, [("internet2", True)])
        self.assertIn('data-wan-picked>internet2</div>', page)
        self.assertIn("owrtremote.main.wan_interface=internet2", self.directory.joinpath("config").read_text())

    def test_empty_legacy_value_uses_wan_and_keeps_offline_choices(self):
        self.select("")
        self.network(["modem4g", "wwan"], [])
        _, form = self.request()
        self.assertEqual(form.options, [("wan", True), ("modem4g", False), ("wwan", False)])

    def test_invalid_runtime_names_are_filtered_and_saved_value_is_escaped(self):
        self.select('old"<&interface')
        self.network(["wan"], ["wan", 'evil"><script>', "two names"])
        page, form = self.request()
        self.assertEqual(form.options, [('old"<&interface', True), ("wan", False)])
        self.assertNotIn("<script>", page)
        self.assertIn('value="old&quot;&lt;&amp;interface"', page)

    def test_form_saves_selected_modem_and_preserves_other_fields(self):
        _, form = self.request()
        form.fields.update({"action": "save", "wan_interface": "modem4g"})
        page, saved = self.request(form.fields)
        self.assertEqual(saved.fields["wan_interface"], ["modem4g"])
        self.assertIn("Настройки сохранены", page)
        config = self.directory.joinpath("config").read_text()
        for row in ("wan_interface=modem4g", "wan_direct=1", "vps_host=192.0.2.25", "vps_host_mode=manual", "router_name=Test router"):
            self.assertIn("owrtremote.main." + row + "\n", config)
        self.assertEqual(self.directory.joinpath("commits").read_text(), "commit owrtremote\n")
        self.assertEqual(self.directory.joinpath("network").read_text().count("=interface\n"), 4)

    def test_two_or_three_selections_save_and_reopen_without_losing_choices(self):
        _, form = self.request()
        form.fields.update({"action": "save", "wan_interface": ["wan", "wwan", "modem4g", "wan"]})
        _, saved = self.request(form.fields)
        self.assertEqual(set(saved.fields["wan_interface"]), {"wan", "wwan", "modem4g"})
        self.assertIn("owrtremote.main.wan_interface=wan wwan modem4g\n", self.directory.joinpath("config").read_text())
        _, reopened = self.request()
        self.assertEqual(set(reopened.fields["wan_interface"]), {"wan", "wwan", "modem4g"})
        self.assertEqual(reopened.fields["wan_interface"][0], "wan")

    def test_empty_or_invalid_selection_is_rejected_before_configuration_changes(self):
        before = self.directory.joinpath("config").read_bytes()
        for choice in ([], ["wan", "invalid interface"], ["wan", "bad;name"]):
            with self.subTest(choice=choice):
                page, _ = self.request({"action": "save_start", "wan_interface_picker": "1", "wan_direct": "1", "wan_interface": choice})
                self.assertIn("Настройки не сохранены", page)
                self.assertEqual(self.directory.joinpath("config").read_bytes(), before)
                self.assertFalse(self.directory.joinpath("commits").exists())

    def test_unauthorized_request_cannot_list_or_save_interfaces(self):
        page, form = self.request({"action": "save", "wan_interface": "modem4g"}, key="wrong-key")
        self.assertIn("Доступ запрещен", page)
        self.assertFalse(form.options)
        self.assertIn("owrtremote.main.wan_interface=wan\n", self.directory.joinpath("config").read_text())


if __name__ == "__main__":
    unittest.main()
