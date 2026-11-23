#!/usr/bin/env python3
"""Validate fixture behavior without a router, VPS, Android SDK, or third-party modules."""

import base64
import http.client
import io
import json
import re
import socket
import tarfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import server


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def login(self):
        status, headers, body = self.request("GET", "/login")
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
        fields = {"csrf": csrf, "username": server.TEST_USERNAME, "password": server.TEST_PASSWORD,
                  "captcha_answer": server.TEST_CAPTCHA, "otp": server.TEST_OTP}
        headers = {"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"}
        status, response_headers, _ = self.request("POST", "/login", urlencode(fields), headers)
        self.assertEqual(status, 303)
        self.assertEqual(response_headers["Location"], "/")
        return cookie, csrf

    def test_login_checks_captcha_2fa_and_csrf_and_preserves_http_only_cookie(self):
        status, headers, body = self.request("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
        fields = {"csrf": csrf, "username": server.TEST_USERNAME, "password": server.TEST_PASSWORD,
                  "captcha_answer": "wrong", "otp": server.TEST_OTP}
        req_headers = {"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"}
        self.assertEqual(self.request("POST", "/login", urlencode(fields), req_headers)[0], 401)
        fields.update(captcha_answer=server.TEST_CAPTCHA, otp="wrong")
        self.assertEqual(self.request("POST", "/login", urlencode(fields), req_headers)[0], 401)
        fields.update(otp=server.TEST_OTP, csrf="wrong")
        self.assertEqual(self.request("POST", "/login", urlencode(fields), req_headers)[0], 403)
        fields["csrf"] = csrf
        self.assertEqual(self.request("POST", "/login", urlencode(fields), req_headers)[0], 303)
        status, _, body = self.request("GET", "/api/session", headers={"Cookie": cookie})
        self.assertTrue(json.loads(body)["authenticated"])
        self.assertEqual(status, 200)

    def test_child_download_upload_and_terminal_share_session(self):
        cookie, csrf = self.login()
        headers = {"Cookie": cookie}
        for path in ("/", "/child?case=admin", "/oauth/popup", "/terminal", "/history/1", "/history/2"):
            self.assertEqual(self.request("GET", path, headers=headers)[0], 200, path)
        status, response_headers, archive = self.request("GET", "/download", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn("attachment", response_headers["Content-Disposition"])
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            self.assertEqual(tar.getnames(), ["FIXTURE-ONLY.txt"])
        boundary = "OWRTFixtureBoundary"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="csrf"\r\n\r\n{csrf}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="images"; filename="test.png"\r\n'
                f'Content-Type: image/png\r\n\r\nnot-a-real-png\r\n--{boundary}--\r\n').encode()
        status, _, response = self.request("POST", "/upload", body, {
            "Cookie": cookie, "Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(status, 200)
        self.assertIn(b"test.png", response)
        self.assertIn(b"&quot;bytes&quot;: 14", response)
        status, _, body = self.request("GET", "/api/ssh/session", headers=headers)
        self.assertEqual(status, 200)
        sid = json.loads(body)["sid"]
        headers.update({"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(self.request("POST", "/api/ssh/write", urlencode({"csrf": csrf, "sid": sid, "data": "hello"}), headers)[0], 200)
        status, _, body = self.request("GET", "/api/ssh/read?sid=" + sid, headers=headers)
        self.assertIn("echo: hello", json.loads(body)["data"])
        self.assertEqual(status, 200)

    def test_csrf_logout_expiry_and_error_endpoints(self):
        self.assertEqual(self.request("GET", "/")[0], 303)
        self.assertEqual(self.request("GET", "/api/session")[0], 401)
        cookie, csrf = self.login()
        headers = {"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"}
        self.assertEqual(self.request("POST", "/api/check-csrf", "csrf=wrong", headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/check-csrf", urlencode({"csrf": csrf}), headers)[0], 200)
        self.assertEqual(self.request("GET", "/error")[0], 503)
        self.assertEqual(self.request("GET", "/timeout?seconds=0")[0], 200)
        self.assertEqual(self.request("GET", "/missing.js")[0], 404)
        self.assertEqual(self.request("GET", "/logout", headers=headers)[0], 303)
        self.assertEqual(self.request("GET", "/api/session", headers=headers)[0], 401)
        cookie, _ = self.login()
        self.assertEqual(self.request("GET", "/expire", headers={"Cookie": cookie})[0], 303)
        self.assertEqual(self.request("GET", "/api/session", headers={"Cookie": cookie})[0], 401)

    def test_authenticated_websocket_echo(self):
        cookie, _ = self.login()
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        stream = connection.makefile("rb")
        key = base64.b64encode(b"fixture-websocket").decode()
        connection.sendall((f"GET /ws/echo HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                            f"Cookie: {cookie}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                            f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n").encode())
        self.assertIn(b"101", stream.readline())
        while stream.readline() != b"\r\n":
            pass
        payload, mask = b"hello", b"ABCD"
        frame = bytes([0x81, 0x80 | len(payload)]) + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
        connection.sendall(frame)
        first = stream.read(2)
        self.assertEqual(first[0], 0x81)
        self.assertEqual(stream.read(first[1]), b"echo: hello")
        connection.sendall(bytes([0x88, 0x80]) + mask)
        stream.close()
        connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
