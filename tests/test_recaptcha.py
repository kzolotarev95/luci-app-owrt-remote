"""Login/settings regression tests. Google responses are mocked; no real keys.

Run: python -m unittest discover -s tests -v
The deployment helper can load a staged hub via OWRT_RECAPTCHA_HUB_SOURCE.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock


SOURCE = Path(os.environ.get("OWRT_RECAPTCHA_HUB_SOURCE", Path(__file__).resolve().parents[1] / "vps" / "owrt-remote-hub.py"))
STATE = tempfile.TemporaryDirectory(prefix="owrt-recaptcha-tests-")
spec = importlib.util.spec_from_file_location("recaptcha_test_hub", SOURCE)
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {"OWRT_REMOTE_STATE_DIR": STATE.name}):
    spec.loader.exec_module(hub)


def configured_auth(mode=hub.CAPTCHA_MODE_RECAPTCHA_V3, score=0.5):
    return hub.normalize_auth_state({
        "username": "test-owner",
        "captcha": {"mode": mode, "site_key": "test-site", "secret_key": "test-secret", "min_score": score},
    })


class RecaptchaTests(unittest.TestCase):
    def verify(self, result, **kwargs):
        with mock.patch.object(hub, "oauth_fetch_json", return_value=result) as google:
            answer = hub.verify_recaptcha_token("test-secret", "test-token", expected_hostname="hub.example", expected_action="login", **kwargs)
        return answer, google

    def test_modes_and_old_v2_settings_are_preserved(self):
        self.assertEqual(hub.sanitize_captcha_mode("recaptcha"), hub.CAPTCHA_MODE_RECAPTCHA)
        self.assertEqual(hub.sanitize_captcha_mode("recaptcha_v3"), hub.CAPTCHA_MODE_RECAPTCHA_V3)
        self.assertEqual(hub.sanitize_captcha_mode("unknown"), hub.CAPTCHA_MODE_DIGITS)
        old = hub.sanitize_captcha_state({"mode": "recaptcha", "site_key": "old-site", "secret_key": "old-secret"})
        self.assertEqual(old["mode"], "recaptcha")
        self.assertEqual(old["min_score"], 0.5)
        self.assertEqual(hub.effective_captcha_state({"captcha": {"mode": "recaptcha_v3"}})["effective_mode"], "digits")

    def test_invalid_thresholds_are_rejected(self):
        for value in (True, None, "", "nan", "inf", -0.1, 1.1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                hub.parse_recaptcha_score(value)
        self.assertEqual(hub.parse_recaptcha_score("0"), 0)
        self.assertEqual(hub.parse_recaptcha_score("1"), 1)
        self.assertEqual(hub.sanitize_captcha_state({"min_score": "nan"})["min_score"], 0.5)

    def test_v3_checks_success_action_hostname_and_score(self):
        good = {"success": True, "hostname": "hub.example", "action": "login", "score": 0.5}
        self.assertTrue(self.verify(good)[0][0])
        for patch in ({"score": 0.49}, {"score": None}, {"score": True}, {"score": "0.9"}, {"score": float("nan")},
                      {"score": 1.1}, {"action": "other"}, {"action": ""}, {"hostname": "other.example"},
                      {"hostname": ""}, {"success": False}, {"success": "true"}):
            with self.subTest(patch=patch):
                self.assertFalse(self.verify({**good, **patch})[0][0])
        self.assertFalse(self.verify({k: v for k, v in good.items() if k != "score"})[0][0])
        self.assertFalse(self.verify(good, min_score=0.6)[0][0])
        self.assertFalse(self.verify([])[0][0])

    def test_v2_does_not_require_v3_fields(self):
        with mock.patch.object(hub, "oauth_fetch_json", return_value={"success": True, "hostname": "hub.example"}):
            self.assertTrue(hub.verify_recaptcha_token("secret", "token", expected_hostname="hub.example")[0])

    def test_empty_expired_and_network_error_fail_closed(self):
        with mock.patch.object(hub, "oauth_fetch_json") as google:
            self.assertFalse(hub.verify_recaptcha_token("secret", "")[0])
            google.assert_not_called()
        self.assertFalse(self.verify({"success": False, "error-codes": ["timeout-or-duplicate"]})[0][0])
        with mock.patch.object(hub, "oauth_fetch_json", side_effect=TimeoutError("unavailable")):
            self.assertFalse(hub.verify_recaptcha_token("secret", "token")[0])

    def test_google_receives_secret_token_and_optional_ip(self):
        with mock.patch.object(hub, "oauth_fetch_json", return_value={"success": True}) as google:
            hub.verify_recaptcha_token("secret", "token", remote_ip="192.0.2.1")
        google.assert_called_once_with(hub.RECAPTCHA_VERIFY_URL, method="POST", data={"secret": "secret", "response": "token", "remoteip": "192.0.2.1"})

    def test_handler_uses_server_action_and_threshold(self):
        handler = object.__new__(hub.Handler)
        handler.client_ip = lambda: "192.0.2.1"
        handler.request_host_name = lambda: "hub.example"
        with mock.patch.object(hub, "verify_recaptcha_token", return_value=(True, "")) as verify:
            self.assertTrue(handler.verify_login_captcha({"g-recaptcha-response": "token", "action": "evil", "score": 1}, configured_auth(score=0.7))[0])
        self.assertEqual(verify.call_args.kwargs["expected_action"], "login")
        self.assertEqual(verify.call_args.kwargs["min_score"], 0.7)

    def test_digits_still_work(self):
        handler = object.__new__(hub.Handler)
        with mock.patch.object(hub, "verify_captcha", return_value=True) as verify:
            self.assertTrue(handler.verify_login_captcha({"captcha_token": "token", "captcha_answer": "12345"}, configured_auth("digits"))[0])
        verify.assert_called_once_with("token", "12345")

    def test_login_pages_and_public_meta_hide_secret(self):
        auth = configured_auth()
        self.assertNotIn("secret_key", hub.public_auth_meta(auth)["captcha"])
        self.assertEqual(hub.admin_auth_meta(auth)["captcha"]["min_score"], 0.5)
        with mock.patch.object(hub, "load_auth", return_value=auth):
            page = hub.login_html()
        self.assertIn("api.js?render=test-site", page)
        self.assertIn('data-recaptcha-action="login"', page)
        self.assertIn('name="g-recaptcha-response"', page)
        self.assertNotIn('name="captcha_answer"', page)
        self.assertNotIn('class="g-recaptcha"', page)
        self.assertNotIn("test-secret", page)
        self.assertNotIn("test-secret", hub.modern_login_captcha_html(auth))
        for mode in ("digits", "recaptcha"):
            with mock.patch.object(hub, "load_auth", return_value=configured_auth(mode)):
                page = hub.login_html()
            if mode == "digits":
                self.assertIn('name="captcha_answer"', page)
                self.assertNotIn('src="https://www.google.com/recaptcha', page)
            else:
                self.assertIn('class="g-recaptcha"', page)
                self.assertNotIn("api.js?render=", page)

    def test_untrusted_site_key_is_escaped_in_markup_and_url(self):
        auth = configured_auth()
        auth["captcha"]["site_key"] = '"><script>bad</script>'
        self.assertNotIn('"><script>bad', hub.modern_login_captcha_html(auth))
        self.assertIn("%3Cscript%3E", hub.login_recaptcha_script(auth))

    def test_settings_validate_and_preserve_other_auth(self):
        handler = object.__new__(hub.Handler)
        handler.headers = {}
        handler.send_text = mock.Mock()
        auth = configured_auth("recaptcha")
        payload = {"current_password": "fake-password", "captcha_mode": "recaptcha_v3", "captcha_min_score": "0.7", "captcha_site_key": "new-site", "captcha_secret_key": "new-secret"}
        handler.read_payload = lambda **kwargs: payload
        with mock.patch.object(hub, "load_auth", return_value=auth), mock.patch.object(hub, "verify_login", return_value=True), mock.patch.object(hub, "save_auth_state") as save:
            handler.update_auth()
            self.assertEqual(handler.send_text.call_args.args[0], 200)
            self.assertEqual(save.call_args.args[0]["captcha"]["mode"], "recaptcha_v3")
            self.assertEqual(save.call_args.args[0]["captcha"]["min_score"], 0.7)
            for bad in ("nan", "", "2"):
                save.reset_mock()
                payload["captcha_min_score"] = bad
                handler.update_auth()
                self.assertEqual(handler.send_text.call_args.args[0], 400)
                save.assert_not_called()
            payload.update(captcha_min_score="0.5", captcha_site_key="")
            handler.update_auth()
            self.assertEqual(handler.send_text.call_args.args[0], 400)
            save.assert_not_called()
            payload = {"current_password": "fake-password", "username": "renamed"}
            handler.read_payload = lambda **kwargs: payload
            handler.update_auth()
            self.assertEqual(save.call_args.args[0]["captcha"]["mode"], "recaptcha")
        with mock.patch.object(hub, "load_auth", return_value=auth), mock.patch.object(hub, "verify_login", return_value=False), mock.patch.object(hub, "save_auth_state") as save:
            handler.update_auth()
            self.assertEqual(handler.send_text.call_args.args[0], 403)
            save.assert_not_called()

    def test_digits_remove_keys_even_if_old_browser_sends_them(self):
        handler = object.__new__(hub.Handler)
        handler.headers = {}
        handler.send_text = mock.Mock()
        payload = {"current_password": "fake-password", "captcha_mode": "digits", "captcha_site_key": "old-site", "captcha_secret_key": "old-secret"}
        handler.read_payload = lambda **kwargs: payload
        with mock.patch.object(hub, "load_auth", return_value=configured_auth()), mock.patch.object(hub, "verify_login", return_value=True), mock.patch.object(hub, "save_auth_state") as save:
            handler.update_auth()
        self.assertEqual(handler.send_text.call_args.args[0], 200)
        captcha = save.call_args.args[0]["captcha"]
        self.assertEqual(captcha["mode"], "digits")
        self.assertEqual(captcha["site_key"], "")
        self.assertEqual(captcha["secret_key"], "")

    def test_switching_versions_cannot_reuse_old_keys(self):
        handler = object.__new__(hub.Handler)
        handler.send_text = mock.Mock()
        for before, after in (("recaptcha_v3", "recaptcha"), ("recaptcha", "recaptcha_v3")):
            with self.subTest(before=before, after=after):
                handler.read_payload = lambda **kwargs: {"current_password": "fake-password", "captcha_mode": after}
                with mock.patch.object(hub, "load_auth", return_value=configured_auth(before)), mock.patch.object(hub, "verify_login", return_value=True), mock.patch.object(hub, "save_auth_state") as save:
                    handler.update_auth()
                self.assertEqual(handler.send_text.call_args.args[0], 400)
                save.assert_not_called()

    def test_empty_form_fields_are_not_restored_from_old_keys(self):
        handler = object.__new__(hub.Handler)
        handler.send_text = mock.Mock()
        handler.headers = {"Content-Type": "application/x-www-form-urlencoded"}
        handler.read_body = lambda: b"current_password=fake-password&captcha_mode=recaptcha&captcha_site_key=&captcha_secret_key="
        self.assertEqual(handler.read_payload(keep_blank_values=True)["captcha_site_key"], "")
        with mock.patch.object(hub, "load_auth", return_value=configured_auth("recaptcha")), mock.patch.object(hub, "verify_login", return_value=True), mock.patch.object(hub, "save_auth_state") as save:
            handler.update_auth()
        self.assertEqual(handler.send_text.call_args.args[0], 400)
        save.assert_not_called()

    def test_digits_key_removal_persists_after_auth_reload(self):
        with tempfile.TemporaryDirectory(prefix="owrt-key-reset-") as temp, mock.patch.object(hub, "AUTH_FILE", Path(temp) / "hub-auth.json"):
            hub.save_auth_state(configured_auth())
            handler = object.__new__(hub.Handler)
            handler.send_text = mock.Mock()
            handler.headers = {"Content-Type": "application/x-www-form-urlencoded"}
            handler.read_body = lambda: b"current_password=fake-password&captcha_mode=digits&captcha_site_key=&captcha_secret_key="
            with mock.patch.object(hub, "verify_login", return_value=True):
                handler.update_auth()
            self.assertEqual(handler.send_text.call_args.args[0], 200)
            reloaded = hub.load_auth()["captcha"]
            self.assertEqual(reloaded["mode"], "digits")
            self.assertEqual(reloaded["site_key"], "")
            self.assertEqual(reloaded["secret_key"], "")
            meta = hub.admin_auth_meta(hub.load_auth())["captcha"]
            self.assertFalse(meta["configured"])
            self.assertEqual(meta["site_key"], "")
            self.assertEqual(meta["secret_key"], "")


class QuietHandler(hub.Handler):
    def log_message(self, *args):
        pass


class LoginHttpTests(unittest.TestCase):
    def test_real_login_http_checks_v3_before_creating_cookie(self):
        auth = configured_auth()
        with mock.patch.object(hub, "load_auth", return_value=auth):
            server = hub.HubHTTPServer(("127.0.0.1", 0), QuietHandler)
            server.app = hub.App(Path(STATE.name) / "hub.db", "fake-session", "fake-agent", "")
            server.is_tls = False
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                result = {"success": True, "action": "login", "score": 0.9, "hostname": "127.0.0.1"}
                payload = {"username": "test-owner", "password": "fake-password", "g-recaptcha-response": "token"}
                request = urllib.request.Request(base + "/login", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                # Use an opener that exposes 302 and its Set-Cookie header.
                class NoRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, *args):
                        return None
                opener = urllib.request.build_opener(NoRedirect())
                with mock.patch.object(hub, "oauth_fetch_json", return_value=result), mock.patch.object(hub, "verify_password_login", return_value=(True, "", auth, "password")) as password, mock.patch.object(hub.Handler, "create_login_session", return_value=("new-session", {})) as session:
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        opener.open(request, timeout=5)
                    with caught.exception as response:
                        self.assertEqual(response.code, 302)
                        self.assertIn("new-session", response.headers["Set-Cookie"])
                    password.assert_called_once()
                    session.assert_called_once()
                    result["score"] = 0.1
                    password.reset_mock()
                    session.reset_mock()
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        opener.open(request, timeout=5)
                    with caught.exception as response:
                        self.assertEqual(response.code, 401)
                    password.assert_not_called()
                    session.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--export-js":
        output = Path(sys.argv[2])
        output.mkdir(parents=True, exist_ok=True)
        auth = configured_auth()
        with mock.patch.object(hub, "load_auth", return_value=auth):
            pages = {
                "login": hub.login_html(),
                "dashboard": hub.dashboard_html([], "test-owner", auth_bootstrap={**hub.admin_auth_meta(auth), "is_owner": True}),
            }
        for name, page in pages.items():
            (output / f"{name}.js").write_text("\n".join(re.findall(r"<script>(.*?)</script>", page, re.S)), encoding="utf-8")
        print(f"JavaScript exported to {output}")
    else:
        unittest.main()
