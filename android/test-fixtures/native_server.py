#!/usr/bin/env python3
"""Disposable native Android Hub API fixture. Never imports Hub code or runs commands."""

import argparse
import hashlib
import base64
import gzip
from email.parser import BytesParser
from email.policy import default as email_policy
import html
import json
import re
import secrets
import ssl
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


BASE = "/api/mobile/v1"
COOKIE = "owrt_remote_session"
TEST_USERNAME = "fixture-user"
TEST_VIEWER = "fixture-viewer"
TEST_PASSWORD = "fixture-only-password"
TEST_OTP = "123456"
TEST_CAPTCHA = "2468"
CAPTCHA_TOKEN = "fixture-native-captcha-token-v1"
XRAY_CSRF = "fixture-xray-control-csrf-token-v1"
TTL = 1800
MAX_BODY = 16384
ENDPOINTS = {
    "challenge": BASE + "/login/challenge", "login": BASE + "/login",
    "session": BASE + "/session", "logout": BASE + "/logout",
}


class FixtureState:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}
        self.history = {"main": [], "offline": []}
        self.notes = {"main": "Тестовая заметка о домашнем роутере", "offline": "Резервный роутер"}
        self.note_entries = {router_id: [{"id": "fixture-note", "text": text, "created_at": int(time.time()), "images": []}]
                             for router_id, text in self.notes.items()}
        self.xray = {"main": "running", "offline": "stopped"}
        self.captcha_issued_at = time.time()
        self.fail_logout_once = False
        self.fail_resources_once = False
        self.terminals = {}
        self.restore_count = 0
        self.backup = gzip.compress(b'OWRT native backup fixture\n' * 2000, mtime=0)
        self.late_request = threading.Event()
        self.browser_login = False
        self.totp_enabled = True
        self.users = []
        self.uploaded = b''
        self.router_update_job = None
        self.password_next = False
        self.hub_available=False
        self.hub_update_starts=0
        self.notification_serial = 100
        self.xray_csrf = XRAY_CSRF

    def router(self, router_id, viewer=False):
        online = router_id == "main"
        permissions = {key: not viewer for key in (
            "owner", "ssh", "manage", "notes_edit", "notes_delete", "config", "wol", "traffic", "delete", "diagnose")}
        permissions.update(admin=True, notes=True)
        now = int(time.time())
        note = self.notes[router_id]
        return {
            "id": router_id, "name": "Домашний роутер" if online else "Дача",
            "group_id": "home" if online else "remote", "online": online,
            "last_seen": now if online else now - 7200, "last_seen_iso": "2026-11-24T12:00:00Z",
            "service_enabled": True, "version": "v124", "agent_version": "v124",
            "vps_host": "fixture.invalid", "public_ip": "198.51.100.10",
            "access_flags": ["A"] if viewer else ["A", "B", "G"], "permissions": permissions,
            "notes": note, "notes_preview": (self.note_entries[router_id][-1].get("text") or "Фото") if self.note_entries[router_id] else "", "notes_count": len(self.note_entries[router_id]),
            "has_notes": bool(self.note_entries[router_id]), "notes_entries": self.note_entries[router_id],
            "access_url": f"/access/{router_id}/", "ssh_url": f"/ssh/{router_id}/",
            "config_url": f"/router/{router_id}/config", "notes_url": f"/router/{router_id}/notes",
            "status": {
                "xray": self.xray[router_id], "xray_control": 1, "heartbeat_interval": 30,
                "version": "v124", "agent_version": "v124", "model": "Fixture OpenWrt Router",
                "hostname": "fixture-" + router_id, "ip": "192.168.2.1", "lan_ip": "192.168.2.1",
                "uptime": 86400, "cpu_percent": 12.5, "memory_percent": 42, "service": "enabled",
            },
        }

    def snapshot(self, router_id, advance=False):
        now = int(time.time())
        for command in self.history[router_id]:
            if advance and command["status"] in ("queued", "running"):
                command["polls"] += 1
                command["status"] = "running" if command["polls"] == 1 else "succeeded"
                command["delivered_at"] = command["started_at"] = now
                if command["status"] == "succeeded":
                    command["completed_at"] = now
                    command["result"] = "Fixture: команда подтверждена; shell не запускался."
                    self.xray[router_id] = "stopped" if command["action"] == "stop" else "running"
        history = [{key: value for key, value in item.items() if key not in ("polls", "hidden")}
                   for item in reversed(self.history[router_id]) if not item.get("hidden")]
        router = self.router(router_id)
        return {"ok": True, "router_id": router_id, "name": router["name"], "online": router["online"],
                "last_seen": router["last_seen"], "xray": self.xray[router_id], "supported": True,
                "server_time": now, "heartbeat_interval": 30, "command_timeout_seconds": 180,
                "execution_timeout_seconds": 90, "history": history}


class NativeFixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        self.state = FixtureState()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "OWRTNativeFixture/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Deliberately omit queries, bodies, cookies, and authentication values.
        print("native fixture " + self.command + " " + urlsplit(self.path).path, flush=True)

    def respond(self, status, body=b"", content_type="application/json; charset=utf-8", headers=()):
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

    def json_response(self, status, value, headers=()):
        self.respond(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), headers=headers)

    def error(self, status, code, message):
        self.json_response(status, {"ok": False, "code": code, "error": message})

    def cookie_header(self, token="", clear=False):
        secure = "; Secure" if isinstance(self.connection, ssl.SSLSocket) else ""
        return "Set-Cookie", (f"{COOKIE}={'' if clear else token}; HttpOnly; SameSite=Lax; "
                              f"Path=/; Max-Age={0 if clear else TTL}{secure}")

    def session(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            return "", None
        token = cookies[COOKIE].value if COOKIE in cookies else ""
        with self.server.state.lock:
            state = self.server.state.sessions.get(token)
            if state and state["expires_at"] <= time.time():
                self.server.state.sessions.pop(token, None)
                state = None
            return token, state

    def require_auth(self):
        token, state = self.session()
        if state is None:
            self.error(401, "not_authenticated", "Sign in to fixture Hub")
        return token, state

    def same_origin(self):
        origin = self.headers.get("Origin", "")
        scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
        if origin and origin != scheme + "://" + self.headers.get("Host", ""):
            self.error(403, "origin_forbidden", "Request origin is not allowed")
            return False
        return True

    def read_fields(self):
        length = self.headers.get("Content-Length", "")
        if (self.headers.get("Transfer-Encoding") or not length.isdigit()
                or len(length) > 5 or not 0 < int(length) <= MAX_BODY):
            raise ValueError("Expected a small request")
        body = self.rfile.read(int(length))
        if len(body) != int(length):
            raise ValueError("Incomplete body")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type == "application/json":
            fields = json.loads(body.decode("utf-8"))
            if not isinstance(fields, dict):
                raise ValueError("Expected a JSON object")
            return fields
        if content_type == "application/x-www-form-urlencoded" and urlsplit(self.path).path == "/login":
            return {key: values[-1] for key, values in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}
        raise ValueError("Expected application/json")

    def new_session(self, username):
        token = secrets.token_urlsafe(36)
        state = {"id": secrets.token_hex(8), "username": username, "expires_at": int(time.time()) + TTL,
                 "auth_method": "password" if username == TEST_VIEWER else "password+totp",
                 "csrf": hashlib.sha256((token + ":mobile").encode()).hexdigest()}
        with self.server.state.lock:
            self.server.state.sessions[token] = state
        return token, state

    def session_payload(self, state):
        viewer = state["username"] == TEST_VIEWER
        flags = {"main": ["A"]} if viewer else {"main": ["A", "B", "G"], "offline": ["A", "B", "G"]}
        csrf = {} if viewer else {"xray_control": self.server.state.xray_csrf, "hub_update": "fixture-hub-update-csrf-token-v1",
                                 "router_updates": "fixture-router-update-csrf-token-v1"}
        headers = {} if viewer else {"xray_control": "X-Xray-Control-CSRF", "hub_update": "X-Hub-Update-CSRF",
                                    "router_updates": "X-Router-Update-CSRF"}
        return {"ok": True, "api_version": 1, "account": {"username": state["username"], "is_owner": not viewer,
                "can_add_router": not viewer, "router_ids": list(flags), "router_flags": flags},
                "auth": {"username": state["username"], "totp_enabled": not viewer and self.server.state.totp_enabled, "captcha": {"mode": "digits"}},
                "session": {key: state[key] for key in ("id", "expires_at", "auth_method")},
                "csrf_token": state["csrf"], "csrf_header": "X-Mobile-CSRF", "csrf": csrf, "csrf_headers": headers,
                "links": {"dashboard": "/", "routers": "/api/routers", "router_groups": "/api/router-groups",
                          "sessions": "/api/sessions", "vps_resources": "/api/vps/resources"}}

    def do_GET(self):
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        if path == "/health":
            self.json_response(200, {"ok": True, "fixture": True, "native": True})
            return
        if path == BASE + "/capabilities":
            self.json_response(200, {"ok": True, "api_version": 1, "hub_version": "v124",
                                    "native_password_login": True, "browser_login_url": "/login", "endpoints": ENDPOINTS})
            return
        if path == ENDPOINTS["challenge"]:
            self.server.state.captcha_issued_at = time.time()
            self.json_response(200, {"ok": True, "captcha": {"mode": "digits", "code": TEST_CAPTCHA,
                                    "token": CAPTCHA_TOKEN, "ttl_seconds": 300},
                                    "browser_login_required": self.server.state.browser_login, "browser_login_url": "/login"})
            return
        if path == "/api/auth/meta":
            _, actor = self.session()
            owner = actor is not None and actor['username'] == TEST_USERNAME
            self.json_response(200, {"ok": True, "auth": {"username": TEST_USERNAME, "totp_enabled": self.server.state.totp_enabled,
                                    "is_owner": owner, "delegated_users": self.server.state.users if owner else [],
                                    "router_options": [{"id": "main", "name": "Домашний роутер"}] if owner else [],
                                    "captcha": {"mode": "digits"}, "passkeys_supported": False,
                                    "passkey_count": 0, "ssh_key_count": 0, "ed25519_enabled": False}})
            return
        if path == "/login":
            self.respond(200, ("<!doctype html><meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>Fixture browser login</title><h1>Fixture browser login</h1><form method='post' action='/login'>"
                "<input name='username' placeholder='Username'><input type='password' name='password' placeholder='Password'>"
                "<input name='captcha_answer' placeholder='CAPTCHA 2468'><input name='otp' placeholder='OTP'>"
                "<button>Sign in</button></form>").encode(), "text/html; charset=utf-8")
            return
        token, state = self.require_auth()
        if state is None:
            return
        viewer = state["username"] == TEST_VIEWER
        # Simulate delayed responses outside the state lock so another client can log in.
        if path in ("/api/fixture/late-401", "/api/fixture/late-session"):
            self.server.state.late_request.set()
            data = self.session_payload(state)
            time.sleep(1.5)
            if path.endswith("late-401"):
                self.error(401, "expired", "Old fixture request expired")
            else:
                self.json_response(200, data)
            return
        if re.fullmatch(r"/api/ssh-session/[^/]+/read", path): time.sleep(0.35)
        with self.server.state.lock:
            if path == "/api/fixture/password-next":
                self.server.state.password_next=True
                self.json_response(200,{"ok":True})
            elif path == ENDPOINTS["session"]:
                self.json_response(200, self.session_payload(state))
            elif re.fullmatch(BASE + r"/router/(main|offline)/notes", path):
                router_id = path.split("/")[-2]
                if viewer and router_id != "main":
                    self.error(403, "forbidden", "Fixture router access required")
                else:
                    self.json_response(200, {"ok": True, "router_id": router_id,
                        "notes_entries": self.server.state.note_entries[router_id], "supports_images": True, "can_append": not viewer, "can_delete": not viewer})
            elif re.fullmatch(BASE+r"/router/(main|offline)/notes-media/[^/]+/[^/]+",path):
                self.respond(200,base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAO0lEQVR4nO3RQREAMAjEwKMKK7FykFUJ4cMvK+CYCXVfZ9NZXY8HBvwBMhEyETIRMhEyETIRMhEyUcgHZIIB+IPjugkAAAAASUVORK5CYII='),'image/png')
            elif path == BASE + "/router-defaults":
                if viewer: self.error(403,"forbidden","Owner required")
                else: self.json_response(200,{"ok":True,"defaults":{"id":"node-2","name":"Роутер node-2","role":"node","vps_host":"fixture.invalid","entry_port":18090,"ssh_entry_port":19090},"groups":[{"id":"home","name":"Дом"}]})
            elif path == "/api/fixture/update-available":
                self.server.state.hub_available=True
                self.json_response(200,{"ok":True})
            elif path == "/api/fixture/new-alert":
                self.server.state.notification_serial += 1
                self.json_response(200,{"ok":True})
            elif path == "/api/notifications/wait":
                serial = self.server.state.notification_serial
                items=[] if int(query.get('after_serial',['0'])[0]) >= serial else [{"serial":serial,"title":"Fixture background alert","body":"Native notification delivered"}]
                self.json_response(200,{"serial":serial,"notifications":items})
            elif path == "/api/routers":
                ids = ["main"] if viewer else ["main", "offline"]
                result = {"routers": [self.server.state.router(item, viewer) for item in ids]}
                if query.get("availability") == ["1"]:
                    now = int(time.time())
                    result["availability"] = {"from": now - 86400, "to": now, "interval": 30, "gap_after": 90,
                                              "points": [[now - offset, 1, len(ids)] for offset in range(300, -1, -30)]}
                self.json_response(200, result)
            elif path == "/api/router-groups":
                self.json_response(200, {"groups": [{"id": "home", "name": "Дом", "sort_order": 0},
                                                     {"id": "remote", "name": "Загородные", "sort_order": 1}]})
            elif path == "/api/vps/resources":
                if self.server.state.fail_resources_once:
                    self.server.state.fail_resources_once = False
                    self.error(503, "resources_unavailable", "Fixture resources unavailable")
                    return
                self.json_response(200, {"ok": True, "cpu": {"percent": 12.5, "cores": 2},
                    "memory": {"percent": 25.0, "used_bytes": 536870912, "total_bytes": 2147483648, "available_bytes": 1610612736},
                    "disk": {"percent": 40.0, "used_bytes": 8589934592, "total_bytes": 21474836480, "free_bytes": 12884901888},
                    "sampled_at": time.time()})
            elif path == "/api/sessions":
                self.json_response(200, {"sessions": [{"id": state["id"], "username": state["username"], "current": True,
                    "client": "Android fixture", "ip": "127.0.0.1", "last_seen": int(time.time()),
                    "auth_method": state["auth_method"], "expires_at": state["expires_at"]}]})
            elif path == "/fixture/expire":
                self.server.state.sessions.pop(token, None)
                self.json_response(200, {"ok": True}, (self.cookie_header(clear=True),))
            elif path == "/api/fixture/logout-fail-next":
                self.server.state.fail_logout_once = True
                self.json_response(200, {"ok": True})
            elif path == "/api/fixture/resources-fail-next":
                self.server.state.fail_resources_once = True
                self.json_response(200, {"ok": True})
            elif path in ("/api/fixture/browser-login-on", "/api/fixture/browser-login-off"):
                self.server.state.browser_login = path.endswith('-on')
                self.json_response(200, {"ok": True})
            elif path == "/api/fixture/totp-disable":
                self.server.state.totp_enabled = False
                self.json_response(200, {"ok": True})
            elif path == "/api/fixture/rotate-csrf":
                self.server.state.xray_csrf = 'fixture-rotated-xray-csrf-1234567890'
                self.json_response(200, {"ok": True})
            elif path == "/api/fixture/late-started":
                self.json_response(200, {"ok": True, "started": self.server.state.late_request.is_set()})
            elif path == "/api/fixture/late-reset":
                self.server.state.late_request.clear()
                self.json_response(200, {"ok": True})
            elif path == "/api/fixture/validation-error":
                self.error(400, "invalid_value", "Проверьте имя роутера: поле не должно быть пустым.")
            elif path == "/api/fixture/false-success":
                self.json_response(200, {"ok": False, "error": "Операция не выполнена: тестовая ошибка."})
            elif path == "/api/fixture/counters":
                self.json_response(200, {"ok": True, "terminals": len(self.server.state.terminals), "restores": self.server.state.restore_count,
                    "uploaded_length": len(self.server.state.uploaded), "uploaded_sha256": hashlib.sha256(self.server.state.uploaded).hexdigest()})
            elif path == "/api/fixture/download":
                self.respond(200, b'Fixture browser download bytes\n', 'text/plain', (("Content-Disposition", 'attachment; filename="fixture.txt"'),))
            elif path == "/api/backup/download":
                if viewer:
                    self.error(403, "forbidden", "Owner required")
                else:
                    self.respond(200, self.server.state.backup, "application/gzip")
            elif re.fullmatch(BASE + r"/router/(main|offline|node-2)/config", path):
                if viewer:
                    self.error(403, "forbidden", "Manage required")
                else:
                    self.json_response(200, {"ok": True, "router_id": path.split('/')[-2],
                        "config": "# Native configuration fixture\nuci set owrtremote.main.router_id='main'\n"})
            elif re.fullmatch(r"/api/ssh/(main|__vps__)/session", path):
                if viewer:
                    self.error(403, "forbidden", "SSH permission required")
                else:
                    router_id = path.split('/')[3]
                    sid = secrets.token_urlsafe(24)
                    self.server.state.terminals[sid] = {"router_id": router_id, "buffer": "root@127.0.0.1 password: " if self.server.state.password_next else "Fixture console ready\n", "awaiting_password":self.server.state.password_next, "username": state['username']}
                    self.server.state.password_next=False
                    self.json_response(200, {"ok": True, "sid": sid, "router_id": router_id})
            elif re.fullmatch(r"/api/ssh-session/[^/]+/read", path):
                terminal = self.server.state.terminals.get(path.split('/')[3])
                if terminal is None:
                    self.error(404, "not_found", "Closed terminal")
                else:
                    data = terminal['buffer']; terminal['buffer'] = ''
                    self.json_response(200, {"ok": True, "data": data, "alive": True})
            elif path == "/api/hub-update" and self.server.state.hub_available:
                self.json_response(200,{"ok":True,"current_version":"v124","remote_version":"v124","checked":True,"available":True,"checking":False,"error":"","remote_revision":"a"*64,"job":{}})
            elif path == "/api/hub-update":
                self.json_response(200, {"ok": True, "current_version": "v124", "remote_version": "v124", "checked": True,
                    "source_incompatible": True, "notice": "Опубликованная сборка пока не поддерживает приложение Android.",
                    "available": False, "checking": False, "error": "", "job": {}})
            elif path == "/api/router-updates":
                self.json_response(200, {"ok": True, "job": self.server.state.router_update_job,
                    "routers": [self.server.state.router('main')]})
            elif path == "/api/notifications":
                self.json_response(200, {"serial": self.server.state.notification_serial, "notifications": [{"id": "fixture-event", "title": "Роутер на связи", "body": "Fixture notification body", "ts": int(time.time())}]})
            elif path == "/fixture/forbidden":
                self.error(403, "forbidden", "Fixture permission failure")
            elif re.fullmatch(r"/api/router/(main|offline)/(xray-control|notes|wol/devices)", path):
                router_id, action = path.split("/")[3], "/".join(path.split("/")[4:])
                if viewer and (router_id != "main" or action != "notes"):
                    self.error(403, "forbidden", "Fixture manage permission required")
                elif action == "xray-control":
                    self.json_response(200, self.server.state.snapshot(router_id, advance=True))
                elif action == "notes":
                    self.json_response(200, {"ok": True, "router": self.server.state.router(router_id, viewer)})
                else:
                    self.json_response(200, {"ok": True, "router_id": router_id, "devices": self.wol_devices()})
            elif path == "/" or re.fullmatch(r"/(?:access|ssh)/(?:main|offline)/?", path) or path.startswith("/router/") or path == "/vps-terminal/":
                self.respond(200, ("<!doctype html><meta name='viewport' content='width=device-width, initial-scale=1'>"
                    "<title>Fixture Hub tool</title><h1>Fixture tool: cookie session OK</h1><p>" + html.escape(state["username"])
                    + "</p><p>Disposable page; no shell or router connection.</p><a id='download' href='/api/fixture/download'>Download fixture</a>"
                    + "<form method='post' action='/api/fixture/upload' enctype='multipart/form-data'><input id='upload-file' type='file' name='file' accept='text/plain'>"
                    + "<button id='upload-submit'>Upload fixture</button></form>").encode(), "text/html; charset=utf-8")
            else:
                self.error(404, "not_found", "Unknown native fixture endpoint")

    @staticmethod
    def wol_devices():
        return [{"mac": "02:00:00:00:00:01", "ip": "192.168.2.100", "name": "Fixture PC", "hostname": "fixture-pc", "iface": "br-lan"}]

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == '/api/fixture/upload':
            _, actor = self.require_auth()
            if actor is None or not self.same_origin():
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                self.error(400, 'invalid_upload', 'Fixture upload is bounded')
                return
            message = BytesParser(policy=email_policy).parsebytes(('Content-Type: ' + self.headers.get('Content-Type', '') + '\r\n\r\n').encode() + self.rfile.read(length))
            files = [part.get_payload(decode=True) for part in message.iter_parts() if part.get_filename()]
            self.server.state.uploaded = b''.join(files)
            self.respond(200, b'<!doctype html><title>Upload done</title><h1>Fixture upload complete</h1>', 'text/html; charset=utf-8')
            return
        try:
            fields = self.read_fields()
        except (ValueError, UnicodeDecodeError):
            self.close_connection = True
            self.error(400, "invalid_request", "Expected a small JSON object")
            return
        if not self.same_origin():
            return
        if path in (ENDPOINTS["login"], "/login"):
            if any(not isinstance(fields.get(key, ""), str) or len(fields.get(key, "")) > limit
                   for key, limit in {"username": 64, "password": 4096, "otp": 32, "captcha_token": 2048, "captcha_answer": 32}.items()):
                self.error(400, "invalid_request", "Invalid fixture login fields")
            elif (fields.get("captcha_answer") != TEST_CAPTCHA or (path != "/login" and
                  (fields.get("captcha_token") != CAPTCHA_TOKEN or time.time() - self.server.state.captcha_issued_at > 300))):
                self.error(401, "captcha_failed", "Invalid fixture CAPTCHA")
            elif (fields.get("username") not in (TEST_USERNAME, TEST_VIEWER) or fields.get("password") != TEST_PASSWORD
                  or fields.get("username") == TEST_USERNAME and self.server.state.totp_enabled and fields.get("otp") != TEST_OTP):
                self.error(401, "login_failed", "Invalid fixture login or OTP")
            else:
                token, state = self.new_session(fields["username"])
                if path == "/login":
                    self.respond(303, headers=(("Location", "/"), self.cookie_header(token)))
                else:
                    self.json_response(200, {"ok": True, "username": state["username"], "auth_method": state["auth_method"],
                                           "session_url": ENDPOINTS["session"]}, (self.cookie_header(token),))
            return
        token, state = self.require_auth()
        if state is None:
            return
        with self.server.state.lock:
            if path.startswith('/api/router-updates/'):
                if state['username'] != TEST_USERNAME or self.headers.get('X-Router-Update-CSRF') != 'fixture-router-update-csrf-token-v1':
                    self.error(403, 'forbidden', 'Owner and operation CSRF required')
                    return
                if path.endswith('/cancel'):
                    job = self.server.state.router_update_job
                    if job and fields.get('id') == job['id']:
                        job.update(state='finished', cancel_requested=True)
                        job['items'][0].update(state='cancelled', message='Fixture queue cancelled; no command was executed')
                elif path.endswith('/refresh'):
                    self.server.state.router_update_job = None
                else:
                    self.server.state.router_update_job = {'id': 'fixture-queue-1234567890', 'operation': 'reboot' if path.endswith('/reboot') else 'update',
                        'state': 'running', 'cancel_requested': False, 'items': [{'id': 'main', 'name': 'Домашний роутер',
                            'state': 'queued', 'message': 'Fixture queue waiting; no command is executed', 'output': ''}]}
                self.json_response(200, {'ok': True, 'job': self.server.state.router_update_job, 'routers': [self.server.state.router('main')]})
                return
            if path in ('/api/auth/totp/setup', '/api/auth/totp/enable'):
                if fields.get('current_password') != TEST_PASSWORD:
                    self.error(403, 'wrong_password', 'Неверный текущий пароль')
                elif path.endswith('/setup'):
                    self.json_response(200, {'ok': True, 'ticket': 'fixture-totp-ticket-1234567890', 'secret': 'JBSWY3DPEHPK3PXP'})
                elif fields.get('ticket') != 'fixture-totp-ticket-1234567890' or fields.get('code') != TEST_OTP:
                    self.error(400, 'invalid_otp', 'Неверный код 2FA')
                else:
                    self.server.state.totp_enabled = True
                    self.json_response(200, {'ok': True})
                return
            if path in ("/api/hub-update/check","/api/hub-update/start"):
                if state['username'] == TEST_VIEWER or self.headers.get('X-Hub-Update-CSRF') != 'fixture-hub-update-csrf-token-v1':
                    self.error(403,'forbidden','Owner CSRF required')
                else:
                    if path.endswith('/start'): self.server.state.hub_update_starts+=1
                    self.json_response(200,{"ok":True,"current_version":"v124","remote_version":"v124","checked":True,"available":not path.endswith('/start'),"checking":False,"error":"","remote_revision":"a"*64,"job":{"status":"succeeded","message":"Fixture Hub updated; no services restarted"} if path.endswith('/start') else {}})
                return
            if path == "/api/router":
                if state['username'] == TEST_VIEWER: self.error(403,"forbidden","Owner required")
                else: self.json_response(200,{"ok":True,"router":dict(fields,permissions={"manage":True,"config":True})})
                return
            if path == "/api/auth/users/save":
                if state['username'] != TEST_USERNAME or fields.get('current_password') != TEST_PASSWORD:
                    self.error(403, "forbidden", "Неверный пароль владельца")
                else:
                    self.server.state.users.append({"id": "audit-user", "username": fields['username'], "enabled": True,
                        "router_flags": fields.get('router_flags', {}), "router_count": 1, "session_count": 0})
                    self.json_response(200, {"ok": True})
                return
            if path == "/api/backup/restore":
                if state['username'] == TEST_VIEWER:
                    self.error(403, "forbidden", "Owner required")
                elif base64.b64decode(fields.get('archive_b64', ''), validate=True) != self.server.state.backup:
                    self.error(400, "invalid_backup", "Fixture backup differs")
                else:
                    self.server.state.restore_count += 1
                    self.json_response(200, {"ok": True})
                return
            if re.fullmatch(r"/api/ssh-session/[^/]+/(write|close)", path):
                sid, action = path.split('/')[3:5]
                terminal = self.server.state.terminals.get(sid)
                if terminal is None:
                    self.json_response(200, {"ok": True})
                elif action == 'close':
                    self.server.state.terminals.pop(sid)
                    self.json_response(200, {"ok": True})
                else:
                    if terminal.get('awaiting_password'):
                        if fields.get('data','').strip() == 'fixture-ssh-secret':
                            terminal['buffer'] += "\nFixture authenticated SSH\nroot@fixture:~# "
                            terminal['awaiting_password']=False
                        else: terminal['buffer'] += "\nPermission denied\npassword: "
                    else: terminal['buffer'] += "Fixture echo: " + fields.get('data', '').strip() + "\n"
                    self.json_response(200, {"ok": True})
                return
            if path == ENDPOINTS["logout"]:
                if not secrets.compare_digest(self.headers.get("X-Mobile-CSRF", ""), state["csrf"]):
                    self.error(403, "csrf_failed", "Fixture mobile CSRF required")
                    return
                if self.server.state.fail_logout_once:
                    self.server.state.fail_logout_once = False
                    self.error(503, "fixture_logout_unavailable", "Fixture server logout is temporarily unavailable")
                    return
                self.server.state.sessions.pop(token, None)
                self.json_response(200, {"ok": True}, (self.cookie_header(clear=True),))
                return
            if re.fullmatch(BASE + r"/router/(main|offline)/notes/delete", path):
                router_id = path.split("/")[-3]
                if state["username"] != TEST_USERNAME:
                    self.error(403, "owner_required", "Fixture owner required")
                elif not secrets.compare_digest(self.headers.get("X-Mobile-CSRF", ""), state["csrf"]):
                    self.error(403, "csrf_failed", "Fixture mobile CSRF required")
                else:
                    entries = self.server.state.note_entries[router_id]
                    selected = fields.get("note_id")
                    if not any(entry["id"] == selected for entry in entries):
                        self.error(404, "note_not_found", "Note not found")
                        return
                    entries[:] = [entry for entry in entries if entry["id"] != selected]
                    self.server.state.notes[router_id] = entries[-1]["text"] if entries else ""
                    self.json_response(200, {"ok": True, "router_id": router_id, "notes_entries": entries,
                        "can_append": True, "can_delete": True, "supports_images": True, "deleted_note_id": selected})
                return
            if re.fullmatch(BASE + r"/router/(main|offline)/notes", path):
                router_id = path.split("/")[-2]
                if state["username"] == TEST_VIEWER:
                    self.error(403, "forbidden", "Fixture manage permission required")
                elif not secrets.compare_digest(self.headers.get("X-Mobile-CSRF", ""), state["csrf"]):
                    self.error(403, "csrf_failed", "Fixture mobile CSRF required")
                else:
                    text = fields.get("text", "")
                    if not isinstance(text, str) or (not text.strip() and not fields.get("images")) or len(text) > 2000:
                        self.error(400, "invalid_note", "Invalid fixture note")
                        return
                    self.server.state.note_entries[router_id].append({"id": secrets.token_hex(8), "text": text.strip(),
                                                                    "created_at": int(time.time()), "images": [{"file_name":"fixture.png","name":"Fixture photo","content_type":"image/png"} for photo in fields.get("images",[])]})
                    self.server.state.notes[router_id] = text.strip()
                    self.json_response(200, {"ok": True, "router_id": router_id,
                        "notes_entries": self.server.state.note_entries[router_id], "supports_images": True, "can_append": True, "can_delete": True})
                return
            match = re.fullmatch(r"/api/router/(main|offline)/(xray-control|notes|wol|wol/devices)", path)
            if not match:
                self.error(404, "not_found", "Unknown fixture write")
                return
            if state["username"] == TEST_VIEWER:
                self.error(403, "forbidden", "Fixture manage permission required")
                return
            router_id, action = match.groups()
            if action == "xray-control":
                if not secrets.compare_digest(self.headers.get("X-Xray-Control-CSRF", ""), self.server.state.xray_csrf):
                    self.error(403, "csrf_failed", "Fixture Xray CSRF required")
                    return
                command_action, request_id = fields.get("action"), fields.get("request_id")
                history = self.server.state.history[router_id]
                if command_action == "clear_history":
                    for command in history:
                        if command["status"] not in ("queued", "running"):
                            command["hidden"] = True
                elif command_action not in ("start", "stop", "restart") or not re.fullmatch(r"[a-f0-9]{32}", str(request_id or "")):
                    self.error(400, "invalid_command", "Invalid fixture command")
                    return
                else:
                    duplicate = next((item for item in history if item["id"] == request_id), None)
                    if duplicate and duplicate["action"] != command_action:
                        self.error(400, "invalid_command", "Fixture request ID already used")
                        return
                    if not duplicate:
                        if any(item["status"] in ("queued", "running") for item in history):
                            self.error(400, "command_pending", "Previous fixture command is pending")
                            return
                        history.append({"id": request_id, "router_id": router_id, "action": command_action,
                            "status": "queued", "created_at": int(time.time()), "delivered_at": 0, "started_at": 0,
                            "completed_at": 0, "result": "", "polls": 0, "hidden": False})
                self.json_response(200, self.server.state.snapshot(router_id))
            elif action == "notes":
                note = fields.get("notes", "")
                if not isinstance(note, str) or len(note) > 8000:
                    self.error(400, "invalid_note", "Invalid fixture note")
                    return
                self.server.state.notes[router_id] = note
                self.server.state.note_entries[router_id] = ([{"id": secrets.token_hex(8), "text": note,
                    "created_at": int(time.time()), "images": []}] if note else [])
                self.json_response(200, {"ok": True, "router": self.server.state.router(router_id)})
            elif action == "wol/devices":
                self.json_response(200, {"ok": True, "router_id": router_id, "devices": self.wol_devices()})
            elif fields.get("mac") != "02:00:00:00:00:01":
                self.error(400, "invalid_mac", "Use the disposable fixture device")
            else:
                self.json_response(200, {"ok": True, "router_id": router_id, "mac": fields["mac"], "iface": "br-lan",
                                       "message": "Fixture WoL accepted; no packet was sent.", "output": "Fixture: no packet was sent."})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--cert", help="Optional local TLS certificate PEM")
    parser.add_argument("--key", help="Optional local TLS private key PEM")
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error("--cert and --key must be supplied together")
    server = NativeFixtureServer(("127.0.0.1", args.port))
    scheme = "http"
    if args.cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    print(f"Native fixture only: {scheme}://127.0.0.1:{server.server_port}/", flush=True)
    print("Disposable credentials are documented in native README; no Hub, router, VPS, or shell is accessed.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
