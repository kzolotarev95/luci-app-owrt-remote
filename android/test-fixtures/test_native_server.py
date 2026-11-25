#!/usr/bin/env python3
"""Offline, loopback-only native fixture contract tests; no Android or Hub imports."""

import http.client
import json
import threading
import unittest

import native_server as fixture


class NativeFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = fixture.NativeFixtureServer(("127.0.0.1", 0))
        cls.port = cls.httpd.server_port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(2)

    def setUp(self):
        with self.httpd.state.lock:
            self.httpd.state = fixture.FixtureState()

    def request(self, method, path, fields=None, cookie="", headers=None, raw=None):
        supplied = dict(headers or {})
        if cookie:
            supplied["Cookie"] = cookie
        body = raw
        if method == "POST" and raw is None:
            body = json.dumps({} if fields is None else fields).encode()
            supplied["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(method, path, body, supplied)
        response = connection.getresponse()
        status, response_headers, data = response.status, dict(response.getheaders()), response.read()
        connection.close()
        is_json = response_headers.get("Content-Type", "").startswith("application/json")
        return status, response_headers, json.loads(data) if is_json and data else data

    def login(self, username=fixture.TEST_USERNAME, **overrides):
        status, _, challenge = self.request("GET", fixture.ENDPOINTS["challenge"])
        self.assertEqual(status, 200)
        fields = {"username": username, "password": fixture.TEST_PASSWORD, "otp": fixture.TEST_OTP,
                  "captcha_token": challenge["captcha"]["token"], "captcha_answer": fixture.TEST_CAPTCHA}
        fields.update(overrides)
        return self.request("POST", fixture.ENDPOINTS["login"], fields)

    def owner(self):
        status, headers, _ = self.login()
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, session = self.request("GET", fixture.ENDPOINTS["session"], cookie=cookie)
        self.assertEqual(status, 200)
        return cookie, session

    def test_capabilities_challenge_and_cookie_authentication(self):
        status, _, payload = self.request("GET", fixture.BASE + "/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(payload["api_version"], 1)
        self.assertTrue(payload["native_password_login"])
        self.assertEqual(payload["endpoints"], fixture.ENDPOINTS)
        status, _, payload = self.request("GET", fixture.ENDPOINTS["challenge"])
        self.assertEqual(payload["captcha"]["code"], "2468")
        self.assertEqual(payload["captcha"]["mode"], "digits")
        self.assertFalse(payload["browser_login_required"])
        status, _, meta = self.request("GET", "/api/auth/meta")
        self.assertEqual(status, 200)
        self.assertEqual(meta["auth"]["username"], fixture.TEST_USERNAME)
        self.assertTrue(meta["auth"]["totp_enabled"])
        for path in (fixture.ENDPOINTS["session"], "/api/routers", "/api/router/main/xray-control"):
            status, _, error = self.request("GET", path)
            self.assertEqual(status, 401)
            self.assertEqual(error["code"], "not_authenticated")
        status, headers, _ = self.login()
        self.assertEqual(status, 200)
        self.assertIn(fixture.COOKIE + "=", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        self.assertIn("Path=/", headers["Set-Cookie"])
        self.assertNotIn("Domain=", headers["Set-Cookie"])

    def test_login_rejects_bad_password_captcha_and_otp(self):
        for field, code in (("password", "login_failed"), ("otp", "login_failed"),
                            ("captcha_answer", "captcha_failed"), ("captcha_token", "captcha_failed")):
            status, headers, error = self.login(**{field: "wrong"})
            self.assertEqual(status, 401)
            self.assertEqual(error["code"], code)
            self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(self.httpd.state.sessions, {})

    def test_owner_session_router_groups_resources_and_tool_share_cookie(self):
        cookie, session = self.owner()
        self.assertTrue(session["account"]["is_owner"])
        self.assertEqual(session["csrf_header"], "X-Mobile-CSRF")
        self.assertEqual(session["csrf"]["xray_control"], fixture.XRAY_CSRF)
        self.assertEqual(session["csrf_headers"]["xray_control"], "X-Xray-Control-CSRF")
        self.assertNotIn(fixture.TEST_PASSWORD, json.dumps(session))
        self.assertNotIn('"password":', json.dumps(session))
        status, _, routers = self.request("GET", "/api/routers?availability=1", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in routers["routers"]], ["main", "offline"])
        self.assertTrue(routers["routers"][0]["online"])
        self.assertFalse(routers["routers"][1]["online"])
        self.assertTrue(routers["routers"][0]["permissions"]["manage"])
        self.assertIn("availability", routers)
        self.assertEqual(routers["availability"]["interval"], 30)
        self.assertEqual(routers["availability"]["points"][-1][1:], [1, 2])
        status, _, groups = self.request("GET", "/api/router-groups", cookie=cookie)
        self.assertEqual([group["id"] for group in groups["groups"]], ["home", "remote"])
        status, _, resources = self.request("GET", "/api/vps/resources", cookie=cookie)
        self.assertEqual(resources["cpu"]["percent"], 12.5)
        self.assertEqual(resources["memory"]["percent"], 25.0)
        self.assertEqual(resources["disk"]["percent"], 40.0)
        status, _, tool = self.request("GET", "/ssh/main/", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"cookie session OK", tool)

    def test_xray_csrf_pending_completion_and_idempotency(self):
        cookie, session = self.owner()
        path = "/api/router/main/xray-control"
        command = {"action": "stop", "request_id": "a" * 32}
        status, _, error = self.request("POST", path, command, cookie=cookie)
        self.assertEqual(status, 403)
        self.assertEqual(error["code"], "csrf_failed")
        headers = {"X-Xray-Control-CSRF": session["csrf"]["xray_control"]}
        status, _, accepted = self.request("POST", path, command, cookie=cookie, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(accepted["history"][0]["status"], "queued")
        self.assertEqual(accepted["xray"], "running")
        status, _, duplicate = self.request("POST", path, command, cookie=cookie, headers=headers)
        self.assertEqual(len(duplicate["history"]), 1)
        status, _, first = self.request("GET", path, cookie=cookie)
        self.assertEqual(first["history"][0]["status"], "running")
        status, _, second = self.request("GET", path, cookie=cookie)
        self.assertEqual(second["history"][0]["status"], "succeeded")
        self.assertEqual(second["xray"], "stopped")
        self.assertIn("shell не запускался", second["history"][0]["result"])
        status, _, cleared = self.request("POST", path, {"action": "clear_history"}, cookie=cookie, headers=headers)
        self.assertEqual(cleared["history"], [])

    def test_viewer_permissions_and_csrf_metadata(self):
        status, headers, _ = self.login(username=fixture.TEST_VIEWER, otp="")
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        _, _, session = self.request("GET", fixture.ENDPOINTS["session"], cookie=cookie)
        self.assertFalse(session["account"]["is_owner"])
        self.assertFalse(session["auth"]["totp_enabled"])
        self.assertEqual(session["csrf"], {})
        _, _, routers = self.request("GET", "/api/routers", cookie=cookie)
        self.assertEqual([row["id"] for row in routers["routers"]], ["main"])
        self.assertFalse(routers["routers"][0]["permissions"]["manage"])
        status, _, _ = self.request("POST", "/api/router/main/notes", {"notes": "forbidden"}, cookie=cookie)
        self.assertEqual(status, 403)
        # A permission failure preserves the session.
        status, _, _ = self.request("GET", fixture.ENDPOINTS["session"], cookie=cookie)
        self.assertEqual(status, 200)

    def test_notes_wol_are_data_only_and_retired_logs_are_absent(self):
        cookie, session = self.owner()
        notes_path = fixture.BASE + "/router/main/notes"
        status, _, old_notes = self.request("GET", notes_path, cookie=cookie)
        self.assertEqual(status, 200)
        previous = list(old_notes["notes_entries"])
        status, _, _ = self.request("POST", notes_path, {"text": "Новая тестовая заметка"}, cookie=cookie)
        self.assertEqual(status, 403)
        status, _, saved = self.request("POST", notes_path, {"text": "Новая тестовая заметка"}, cookie=cookie,
                                       headers={"X-Mobile-CSRF": session["csrf_token"]})
        self.assertEqual(status, 200)
        self.assertEqual(saved["router_id"], "main")
        self.assertEqual(saved["notes_entries"][:-1], previous)
        self.assertEqual(saved["notes_entries"][-1]["text"], "Новая тестовая заметка")
        status, _, logs = self.request("GET", "/api/router/main/logs", cookie=cookie)
        self.assertEqual(status, 404)
        status, _, devices = self.request("GET", "/api/router/main/wol/devices", cookie=cookie)
        self.assertEqual(status, 200)
        device = devices["devices"][0]
        status, _, sent = self.request("POST", "/api/router/main/wol", {"mac": device["mac"]}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("no packet was sent", sent["message"])

    def test_logout_requires_mobile_csrf_and_revokes_cookie(self):
        cookie, session = self.owner()
        status, _, _ = self.request("POST", fixture.ENDPOINTS["logout"], cookie=cookie)
        self.assertEqual(status, 403)
        status, headers, _ = self.request("POST", fixture.ENDPOINTS["logout"], cookie=cookie,
                                          headers={"X-Mobile-CSRF": session["csrf_token"]})
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        status, _, _ = self.request("GET", "/api/routers", cookie=cookie)
        self.assertEqual(status, 401)

    def test_expiry_and_cross_origin_write_rejection(self):
        cookie, _ = self.owner()
        status, _, _ = self.request("POST", "/api/router/main/notes", {"notes": "wrong origin"}, cookie=cookie,
                                    headers={"Origin": "https://other.invalid"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/fixture/expire", cookie=cookie)
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", fixture.ENDPOINTS["session"], cookie=cookie)
        self.assertEqual(status, 401)

    def test_simulated_logout_failure_leaves_server_session_unconfirmed(self):
        cookie, session = self.owner()
        status, _, _ = self.request("GET", "/api/fixture/logout-fail-next", cookie=cookie)
        self.assertEqual(status, 200)
        status, _, _ = self.request("POST", fixture.ENDPOINTS["logout"], cookie=cookie,
                                    headers={"X-Mobile-CSRF": session["csrf_token"]})
        self.assertEqual(status, 503)
        status, _, _ = self.request("GET", fixture.ENDPOINTS["session"], cookie=cookie)
        self.assertEqual(status, 200)

    def test_malformed_json_and_unknown_paths_do_not_run_operations(self):
        status, _, _ = self.request("POST", fixture.ENDPOINTS["login"], raw=b"[]", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", fixture.ENDPOINTS["login"], raw=b"{", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        cookie, _ = self.owner()
        status, _, _ = self.request("POST", "/api/run-shell", {"command": "never execute"}, cookie=cookie)
        self.assertEqual(status, 404)
        self.assertEqual(self.httpd.state.history["main"], [])


if __name__ == "__main__":
    unittest.main()
