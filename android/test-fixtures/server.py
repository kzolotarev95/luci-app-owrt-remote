#!/usr/bin/env python3
"""Local, disposable Hub-like pages for the Android wrapper. No Hub imports or data."""

import argparse
import base64
import hashlib
import html
import io
import json
import secrets
import socket
import ssl
import struct
import tarfile
import threading
import time
from email.parser import BytesParser
from email.policy import default as email_policy
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent
COOKIE = "owrt_fixture_session"
SESSIONS = {}
TERMINALS = {}
LOCK = threading.Lock()
TTL = 1800
MAX_BODY = 8 * 1024 * 1024
TEST_USERNAME = "fixture-user"
TEST_PASSWORD = "fixture-only-password"
TEST_OTP = "123456"
TEST_CAPTCHA = "2468"


def markup(template, **values):
    text = (ROOT / template).read_text(encoding="utf-8")
    for name, value in values.items():
        text = text.replace("@@" + name.upper() + "@@", html.escape(str(value), quote=True))
    return text.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "OWRTAndroidFixture/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Requests only: never print form bodies, cookies or authorization headers.
        print("fixture " + (fmt % args), flush=True)

    def respond(self, status, body=b"", content_type="text/html; charset=utf-8", headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def json_response(self, status, value):
        self.respond(status, json.dumps(value).encode(), "application/json; charset=utf-8")

    def redirect(self, location, headers=()):
        self.respond(303, headers=(("Location", location), *headers))

    def session(self, create=False):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        token = cookies[COOKIE].value if COOKIE in cookies else ""
        with LOCK:
            state = SESSIONS.get(token)
            if state and state["expires"] <= time.time():
                SESSIONS.pop(token, None)
                state = None
            if not state and create:
                token = secrets.token_urlsafe(24)
                state = {"csrf": secrets.token_urlsafe(24), "authenticated": False,
                         "expires": time.time() + TTL, "username": ""}
                SESSIONS[token] = state
        return token, state

    def cookie_header(self, token, clear=False):
        secure = "; Secure" if isinstance(self.connection, ssl.SSLSocket) else ""
        return "Set-Cookie", (f"{COOKIE}={'' if clear else token}; HttpOnly; SameSite=Lax; "
                              f"Path=/; Max-Age={0 if clear else TTL}{secure}")

    def require_auth(self, json_api=False):
        token, state = self.session()
        if not state or not state["authenticated"]:
            if json_api:
                self.json_response(401, {"ok": False, "error": "fixture session expired"})
            else:
                self.redirect("/login")
            return None
        return state

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY:
            raise ValueError("fixture body exceeds 8 MiB")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("incomplete request body")
        return body

    def read_fields(self):
        body = self.read_body()
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            message = BytesParser(policy=email_policy).parsebytes(
                b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
            fields, uploads = {}, []
            if message.is_multipart():
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition") or ""
                    data = part.get_payload(decode=True) or b""
                    filename = part.get_filename()
                    if filename is None:
                        fields[name] = data.decode("utf-8", "replace")
                    else:
                        uploads.append({"field": name, "name": filename, "bytes": len(data),
                                        "type": part.get_content_type()})
            return fields, uploads
        if ctype.startswith("application/json"):
            return json.loads(body or b"{}"), []
        return {key: values[-1] for key, values in parse_qs(body.decode(), keep_blank_values=True).items()}, []

    def valid_csrf(self, state, fields):
        supplied = self.headers.get("X-CSRF-Token", "") or str(fields.get("csrf", ""))
        return bool(state and secrets.compare_digest(state["csrf"], supplied))

    def login_page(self, error="", status=200):
        token, state = self.session(create=True)
        self.respond(status, markup("login.html", csrf=state["csrf"], error=error),
                     headers=(self.cookie_header(token),))

    def do_GET(self):
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        if path == "/health":
            self.json_response(200, {"ok": True, "fixture": True})
            return
        if path in ("/login", "/2fa"):
            self.login_page()
            return
        if path == "/logout":
            token, _ = self.session()
            with LOCK:
                SESSIONS.pop(token, None)
            self.redirect("/login", (self.cookie_header(token, clear=True),))
            return
        if path == "/error":
            self.respond(503, b"<!doctype html><title>Fixture HTTP error</title><h1 id='httpError'>Fixture HTTP 503</h1><a href='/'>Return</a>")
            return
        if path == "/timeout":
            # A real stalled navigation, capped so a fixture cannot leave permanent workers.
            delay = min(90, max(0, float(query.get("seconds", ["45"])[0])))
            time.sleep(delay)
            self.respond(200, b"<!doctype html><title>Fixture timeout complete</title><h1>Delayed fixture response</h1>")
            return
        if path == "/disconnect":
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if path == "/missing.js":
            self.respond(404, b"/* intentionally missing fixture subresource */", "text/javascript")
            return
        if path == "/manifest.webmanifest":
            self.respond(200, json.dumps({"name": "OWRT Android Fixture", "short_name": "Fixture",
                                         "start_url": "/", "display": "standalone"}).encode(),
                         "application/manifest+json")
            return
        if path == "/sw.js":
            # Deliberately no cache: authenticated pages must remain session-bound.
            self.respond(200, b"self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(clients.claim()));",
                         "text/javascript", (("Service-Worker-Allowed", "/"),))
            return
        state = self.require_auth(json_api=path.startswith("/api/") or path.startswith("/ws/"))
        if state is None:
            return
        if path == "/":
            self.respond(200, markup("index.html", csrf=state["csrf"], username=state["username"]))
        elif path in ("/api/session", "/api/sessions"):
            self.json_response(200, {"ok": True, "authenticated": True, "username": state["username"]})
        elif path == "/expire":
            token, _ = self.session()
            with LOCK:
                SESSIONS.pop(token, None)
            self.redirect("/", (self.cookie_header(token, clear=True),))
        elif path == "/child":
            self.respond(200, markup("child.html", case=query.get("case", ["anchor"])[0],
                                    username=state["username"]))
        elif path == "/oauth/popup":
            self.respond(200, markup("oauth.html"))
        elif path in ("/history/1", "/history/2"):
            n = int(path[-1])
            self.respond(200, (f"<!doctype html><meta name='viewport' content='width=device-width, initial-scale=1'>"
                               f"<title>Fixture history {n}</title><h1 id='historyStep'>History {n}</h1>"
                               f"<a id='historyNext' href='/history/{2 if n == 1 else 1}'>Next</a> "
                               "<a href='/'>Dashboard</a>").encode())
        elif path == "/download/redirect":
            self.redirect("/download")
        elif path == "/download":
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                body = b"Disposable Android WebView fixture. This is not a Hub backup.\n"
                entry = tarfile.TarInfo("FIXTURE-ONLY.txt")
                entry.size = len(body)
                archive.addfile(entry, io.BytesIO(body))
            self.respond(200, buffer.getvalue(), "application/gzip",
                         (("Content-Disposition", 'attachment; filename="owrt-fixture-backup.tar.gz"'),))
        elif path == "/terminal":
            self.respond(200, markup("terminal.html", csrf=state["csrf"]))
        elif path == "/api/ssh/session":
            sid = secrets.token_urlsafe(12)
            with LOCK:
                TERMINALS[sid] = {"csrf": state["csrf"], "data": "Fixture terminal connected\r\n", "alive": True}
            self.json_response(200, {"ok": True, "sid": sid})
        elif path == "/api/ssh/read":
            sid = query.get("sid", [""])[0]
            with LOCK:
                term = TERMINALS.get(sid)
                if term and term["csrf"] == state["csrf"]:
                    data = term["data"]
                    term["data"] = ""
                    response = {"ok": True, "alive": term["alive"], "data": data}
                else:
                    response = {"ok": False, "error": "fixture terminal not found"}
            self.json_response(200 if response["ok"] else 404, response)
        elif path == "/ws/echo":
            self.websocket_echo()
        else:
            self.respond(404, b"<!doctype html><title>Fixture not found</title><h1>Fixture 404</h1>")

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            fields, uploads = self.read_fields()
        except (ValueError, json.JSONDecodeError):
            self.json_response(400, {"ok": False, "error": "invalid fixture request"})
            return
        token, state = self.session()
        if path == "/login":
            if not self.valid_csrf(state, fields):
                self.login_page("CSRF check failed", 403)
            elif (fields.get("username") != TEST_USERNAME or fields.get("password") != TEST_PASSWORD):
                self.login_page("Invalid fixture username or password", 401)
            elif fields.get("captcha_answer") != TEST_CAPTCHA:
                self.login_page("Invalid fixture CAPTCHA", 401)
            elif fields.get("otp") != TEST_OTP:
                self.login_page("Invalid fixture 2FA code", 401)
            else:
                state.update(authenticated=True, username=TEST_USERNAME, expires=time.time() + TTL)
                self.redirect("/", (self.cookie_header(token),))
            return
        if self.require_auth(json_api=True) is None:
            return
        if not self.valid_csrf(state, fields):
            self.json_response(403, {"ok": False, "error": "fixture CSRF check failed"})
            return
        if path == "/api/check-csrf":
            self.json_response(200, {"ok": True, "message": "fixture CSRF accepted"})
        elif path == "/upload":
            report = html.escape(json.dumps(uploads, indent=2))
            self.respond(200, ("<!doctype html><meta name='viewport' content='width=device-width, initial-scale=1'>"
                               "<title>Fixture upload result</title><h1>Fixture uploads received</h1>"
                               f"<pre id='uploadResult'>{report}</pre><a href='/'>Dashboard</a>").encode())
        elif path in ("/api/ssh/write", "/api/ssh/close", "/api/ssh/resize"):
            sid = fields.get("sid", "")
            with LOCK:
                term = TERMINALS.get(sid)
                found = bool(term and term["csrf"] == state["csrf"])
                if found and path.endswith("/write"):
                    term["data"] += "echo: " + str(fields.get("data", "")) + "\r\n"
                elif found and path.endswith("/close"):
                    term["alive"] = False
            self.json_response(200 if found else 404, {"ok": found})
        else:
            self.json_response(404, {"ok": False, "error": "fixture POST not found"})

    def websocket_echo(self):
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key or self.headers.get("Upgrade", "").lower() != "websocket":
            self.json_response(400, {"ok": False, "error": "WebSocket upgrade required"})
            return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.connection.settimeout(45)
        self.close_connection = True
        try:
            while True:
                first = self.rfile.read(2)
                if len(first) != 2:
                    break
                opcode, masked, length = first[0] & 15, first[1] & 128, first[1] & 127
                if length == 126:
                    length = struct.unpack("!H", self.rfile.read(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self.rfile.read(8))[0]
                if not masked or length > 65000 or opcode == 8:
                    self.wfile.write(b"\x88\x00")
                    break
                mask = self.rfile.read(4)
                payload = self.rfile.read(length)
                if len(mask) != 4 or len(payload) != length:
                    break
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
                if opcode not in (1, 2, 9):
                    continue
                response = payload if opcode == 9 else b"echo: " + payload
                header = bytes((0x8A if opcode == 9 else 0x81, len(response))) if len(response) < 126 else bytes((0x81, 126)) + struct.pack("!H", len(response))
                self.wfile.write(header + response)
                self.wfile.flush()
        except (OSError, ValueError, struct.error):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cert", help="Optional local TLS certificate PEM")
    parser.add_argument("--key", help="Optional local TLS private key PEM")
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error("--cert and --key must be supplied together")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    scheme = "http"
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    print(f"Fixture only: {scheme}://127.0.0.1:{server.server_port}/", flush=True)
    print(f"Fake login: {TEST_USERNAME} / {TEST_PASSWORD}; CAPTCHA {TEST_CAPTCHA}; 2FA {TEST_OTP}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
