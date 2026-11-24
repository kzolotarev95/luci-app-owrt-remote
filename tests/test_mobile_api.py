"""Native JSON authentication uses real Hub captcha, TOTP and session checks."""

import http.client
from contextlib import nullcontext
import hashlib
import base64
import zlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
STATE = tempfile.TemporaryDirectory(prefix="owrt-native-mobile-")
sys.path.insert(0, str(ROOT / "vps"))
import mobile_api

spec = importlib.util.spec_from_file_location("native_mobile_hub", ROOT / "vps/owrt-remote-hub.py")
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {"OWRT_REMOTE_STATE_DIR": STATE.name}):
    spec.loader.exec_module(hub)


class FixtureHandler(hub.Handler):
    def log_message(self, *args):
        pass


class MobileApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=STATE.name)
        self.addCleanup(self.temp.cleanup)
        self.auth = hub.normalize_auth_state({
            "username": "fixture-owner",
            "password": hub.password_digest("fixture-owner-password", iterations=1000),
            "delegated_users": [{
                "id": "fixture-viewer-id", "username": "fixture-viewer", "enabled": True,
                "password": hub.password_digest("fixture-viewer-password", iterations=1000),
                "router_flags": {"allowed-router": ["A"]},
            }],
        })
        self.sessions_file = Path(self.temp.name) / "sessions.json"
        for patcher in (
            mock.patch.object(hub, "load_auth", side_effect=lambda: self.auth),
            mock.patch.object(hub, "session_token", return_value="fixture-captcha-signing-key"),
            mock.patch.object(hub, "SESSIONS_FILE", self.sessions_file),
            mock.patch.object(hub, "add_notification"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = hub.HubHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.server.app = SimpleNamespace(
            session_token="fixture-legacy-cookie", public_url="", xray_control_csrf="fixture-xray-csrf",
            hub_update=SimpleNamespace(csrf="fixture-hub-update-csrf"),
            router_updates=SimpleNamespace(csrf="fixture-router-updates-csrf"),
        )
        self.server.is_tls = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, payload=None, *, cookie=None, headers=None, method=None, raw=None):
        request_headers = {"User-Agent": "OWRT Native Android fixture"}
        if cookie:
            request_headers["Cookie"] = cookie
        if payload is not None:
            raw = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method or ("POST" if raw is not None else "GET"), path, raw, request_headers)
            response = conn.getresponse()
            data = json.loads(response.read())
            return response.status, data, dict(response.getheaders())
        finally:
            conn.close()

    def challenge(self):
        status, data, _ = self.request(mobile_api.ENDPOINTS["challenge"])
        self.assertEqual(status, 200)
        return data["captcha"]

    def login(self, username="fixture-owner", password="fixture-owner-password", otp="", **kwargs):
        captcha = self.challenge()
        return self.request(mobile_api.ENDPOINTS["login"], {
            "username": username, "password": password, "otp": otp,
            "captcha_token": captcha.get("token", ""), "captcha_answer": captcha.get("code", ""),
        }, **kwargs)

    def cookie(self, **kwargs):
        status, data, headers = self.login(**kwargs)
        self.assertEqual((status, data["ok"]), (200, True), data)
        return headers["Set-Cookie"].split(";", 1)[0]

    def notes_database(self):
        database = Path(self.temp.name) / "notes.sqlite"
        self.server.app.conn = lambda: hub.connect(database)
        with self.server.app.conn() as conn:
            hub.init_db(conn)
            for router_id in ("allowed-router", "private-router"):
                hub.upsert_router(conn, {"id": router_id, "name": router_id})
            original = hub.build_router_note_entry("Existing note", created_at=1700000000, note_id="old-note", images=[{
                "id": "existing-image", "file_name": "existing.png", "content_type": "image/png",
                "name": "Existing photo", "size": 321,
            }])
            conn.execute("update routers set notes=? where id='allowed-router'", (hub.encode_router_notes_entries([original]),))
            conn.commit()
        return original

    def test_router_defaults_uses_all_ports_and_requires_add_permission(self):
        self.notes_database()
        with self.server.app.conn() as conn:
            hub.upsert_router(conn, {'id': 'main', 'name': 'Main', 'entry_port':18080, 'ssh_entry_port':19080})
            hub.upsert_router(conn, {'id': 'node-2', 'name': 'Private', 'entry_port':18090, 'ssh_entry_port':19090})
            conn.execute("update routers set tachyon_entry_port=18100 where id='node-2'")
            conn.commit()
        endpoint = mobile_api.BASE + '/router-defaults'
        self.assertEqual(self.request(endpoint)[0],401)
        cookie=self.cookie()
        status,data,_=self.request(endpoint,cookie=cookie)
        self.assertEqual(status,200)
        self.assertEqual(data['defaults']['id'],'node-3')
        self.assertEqual(data['defaults']['entry_port'],18110)
        self.assertEqual(data['defaults']['ssh_entry_port'],19110)
        self.assertNotIn('vless_uuid',json.dumps(data))
        viewer=self.cookie(username='fixture-viewer',password='fixture-viewer-password')
        self.assertEqual(self.request(endpoint,cookie=viewer)[0],403)

    def test_native_photo_note_upload_download_permissions_and_invalid_data(self):
        self.notes_database()
        import tempfile
        with tempfile.TemporaryDirectory() as image_dir, mock.patch.object(hub, 'ROUTER_NOTES_MEDIA_DIR',Path(image_dir)):
            cookie=self.cookie()
            _,session,_=self.request(mobile_api.ENDPOINTS['session'],cookie=cookie)
            endpoint=mobile_api.BASE+'/router/allowed-router/notes'
            raw=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAO0lEQVR4nO3RQREAMAjEwKMKK7FykFUJ4cMvK+CYCXVfZ9NZXY8HBvwBMhEyETIRMhEyETIRMhEyUcgHZIIB+IPjugkAAAAASUVORK5CYII=')
            image={'name':'photo.png','content_type':'image/png','data':base64.b64encode(raw).decode()}
            status,result,_=self.request(endpoint,{'text':'','images':[image]},cookie=cookie,headers={'X-Mobile-CSRF':session['csrf_token']})
            self.assertEqual(status,200,result)
            entry=result['notes_entries'][-1]
            self.assertEqual(len(entry['images']),1)
            self.assertEqual(len(result['notes_entries']),2)
            path=mobile_api.BASE+'/router/allowed-router/notes-media/'+entry['id']+'/'+entry['images'][0]['file_name']
            conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
            conn.request('GET',path,headers={'Cookie':cookie});response=conn.getresponse()
            self.assertEqual(response.status,200);self.assertEqual(response.read(),raw);conn.close()
            self.assertEqual(self.request(path)[0],401)
            viewer=self.cookie(username='fixture-viewer',password='fixture-viewer-password')
            denied=path.replace('allowed-router','private-router')
            self.assertIn(self.request(denied,cookie=viewer)[0],(403,404))
            bad=dict(image,data=base64.b64encode(b'<script>bad</script>').decode())
            self.assertEqual(self.request(endpoint,{'text':'','images':[bad]},cookie=cookie,headers={'X-Mobile-CSRF':session['csrf_token']})[0],400)
            self.assertEqual(self.request(endpoint,{'text':'','images':[image]*5},cookie=cookie,headers={'X-Mobile-CSRF':session['csrf_token']})[0],400)

    def test_native_notes_append_preserves_previous_entries_images_and_router_scope(self):
        original = self.notes_database()
        cookie = self.cookie()
        _, session, _ = self.request(mobile_api.ENDPOINTS["session"], cookie=cookie)
        endpoint = mobile_api.BASE + "/router/allowed-router/notes"
        status, before, _ = self.request(endpoint, cookie=cookie)
        self.assertEqual((status, before["router_id"], before["can_append"]), (200, "allowed-router", True))
        self.assertEqual(before["notes_entries"][0]["id"], "old-note")
        status, after, _ = self.request(endpoint, {"text": "Новая запись"}, cookie=cookie,
                                        headers={"X-Mobile-CSRF": session["csrf_token"]})
        self.assertEqual((status, after["router_id"]), (200, "allowed-router"))
        self.assertEqual(len(after["notes_entries"]), 2)
        self.assertEqual(after["notes_entries"][0]["text"], original["text"])
        self.assertEqual(after["notes_entries"][0]["images"][0]["file_name"], "existing.png")
        self.assertEqual(after["notes_entries"][1]["text"], "Новая запись")
        self.assertNotEqual(after["notes_entries"][1]["id"], original["id"])
        _, other, _ = self.request(mobile_api.BASE + "/router/private-router/notes", cookie=cookie)
        self.assertEqual(other["notes_entries"], [])

    def test_native_config_uses_real_current_hub_bundle_and_selected_router_only(self):
        self.notes_database()
        self.server.app.public_url = "https://fixture-hub.example:8443"
        with self.server.app.conn() as conn:
            conn.execute("update routers set vps_host='fixture-vps.example' where id='allowed-router'")
            private = hub.get_active_router(conn, "private-router")["vless_uuid"]
            conn.commit()
        cookie = self.cookie()
        status, data, headers = self.request(mobile_api.BASE + "/router/allowed-router/config", cookie=cookie)
        self.assertEqual((status, data["router_id"]), (200, "allowed-router"))
        self.assertIn("allowed-router", data["config"])
        self.assertIn("https://fixture-hub.example:8443", data["config"])
        self.assertIn("fixture-vps.example", data["config"])
        self.assertNotIn(private, data["config"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_native_config_requires_live_full_router_access(self):
        self.notes_database()
        endpoint = mobile_api.BASE + "/router/allowed-router/config"
        self.assertEqual(self.request(endpoint)[0], 401)
        cookie = self.cookie(username="fixture-viewer", password="fixture-viewer-password")
        self.assertEqual(self.request(endpoint, cookie=cookie)[0], 403)
        self.auth["delegated_users"][0]["router_flags"] = {"allowed-router": ["G"]}
        self.assertEqual(self.request(endpoint, cookie=cookie)[0], 200)
        self.assertEqual(self.request(mobile_api.BASE + "/router/private-router/config", cookie=cookie)[0], 403)
        self.assertEqual(self.request(endpoint, {}, cookie=cookie)[0], 405)
        self.auth["delegated_users"][0]["enabled"] = False
        self.assertEqual(self.request(endpoint, cookie=cookie)[0], 401)

    def test_native_notes_enforce_revocable_session_router_read_and_write_permissions(self):
        self.notes_database()
        endpoint = mobile_api.BASE + "/router/allowed-router/notes"
        self.assertEqual(self.request(endpoint)[0], 401)
        cookie = self.cookie(username="fixture-viewer", password="fixture-viewer-password")
        _, session, _ = self.request(mobile_api.ENDPOINTS["session"], cookie=cookie)
        status, data, _ = self.request(endpoint, cookie=cookie)
        self.assertEqual((status, data["can_append"]), (200, False))
        self.assertEqual(self.request(mobile_api.BASE + "/router/private-router/notes", cookie=cookie)[0], 403)
        self.assertEqual(self.request(endpoint, {"text": "Forbidden change"}, cookie=cookie,
                                     headers={"X-Mobile-CSRF": session["csrf_token"]})[0], 403)
        _, after, _ = self.request(endpoint, cookie=cookie)
        self.assertEqual(len(after["notes_entries"]), 1)

    def test_native_notes_reject_csrf_cross_origin_and_invalid_bodies_without_mutating(self):
        self.notes_database()
        cookie = self.cookie()
        _, session, _ = self.request(mobile_api.ENDPOINTS["session"], cookie=cookie)
        endpoint = mobile_api.BASE + "/router/allowed-router/notes"
        for headers in ({}, {"X-Mobile-CSRF": "wrong"},
                        {"X-Mobile-CSRF": session["csrf_token"], "Origin": "https://other.invalid"}):
            self.assertEqual(self.request(endpoint, {"text": "Do not store"}, cookie=cookie, headers=headers)[0], 403)
        headers = {"X-Mobile-CSRF": session["csrf_token"]}
        for payload in ({}, {"text": " "}, {"text": 42}, {"text": "x" * 2001}, {"text": "x" * 17000}, []):
            self.assertEqual(self.request(endpoint, payload, cookie=cookie, headers=headers)[0], 400)
        self.assertEqual(self.request(endpoint, cookie=cookie, raw=b"bad", method="POST",
                                     headers={**headers, "Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.request(mobile_api.BASE + "/router/allowed-router%2Fprivate-router/notes", cookie=cookie)[0], 400)
        _, after, _ = self.request(endpoint, cookie=cookie)
        self.assertEqual(len(after["notes_entries"]), 1)

    def test_note_delete_removes_selected_photo_only_and_is_not_replayed(self):
        self.notes_database()
        with tempfile.TemporaryDirectory() as media, mock.patch.object(hub, 'ROUTER_NOTES_MEDIA_DIR', Path(media)):
            cookie = self.cookie()
            _, session, _ = self.request(mobile_api.ENDPOINTS['session'], cookie=cookie)
            headers = {'X-Mobile-CSRF': session['csrf_token']}
            endpoint = mobile_api.BASE + '/router/allowed-router/notes'
            image = {'name': 'photo.png', 'content_type': 'image/png',
                     'data': base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()}
            entries = []
            for text in ('Delete this photo', 'Keep this photo'):
                status, data, _ = self.request(endpoint, {'text': text, 'images': [image]}, cookie=cookie, headers=headers)
                self.assertEqual(status, 200, data)
                entries.append(data['notes_entries'][-1])
            selected, retained = entries
            selected_dir = hub.router_note_media_dir('allowed-router', selected['id'])
            retained_dir = hub.router_note_media_dir('allowed-router', retained['id'])
            self.assertTrue(selected_dir.exists() and retained_dir.exists())
            status, data, _ = self.request(endpoint + '/delete', {'note_id': selected['id']}, cookie=cookie, headers=headers)
            self.assertEqual(status, 200, data)
            self.assertTrue(data['can_delete'])
            self.assertEqual([entry['id'] for entry in data['notes_entries']], ['old-note', retained['id']])
            self.assertFalse(selected_dir.exists())
            self.assertTrue(retained_dir.exists())
            self.assertEqual(self.request(endpoint + '/delete', {'note_id': selected['id']}, cookie=cookie, headers=headers)[0], 404)
            self.assertEqual(self.request(endpoint + '/delete', cookie=cookie)[0], 404)
            self.assertEqual(len(self.request(endpoint, cookie=cookie)[1]['notes_entries']), 2)
            self.assertEqual(self.request(endpoint.replace('allowed-router', 'private-router'), cookie=cookie)[1]['notes_entries'], [])

    def test_note_delete_requires_owner_session_csrf_and_valid_selected_entry(self):
        self.notes_database()
        endpoint = mobile_api.BASE + '/router/allowed-router/notes/delete'
        self.assertEqual(self.request(endpoint, {'note_id': 'old-note'})[0], 401)
        viewer = self.cookie(username='fixture-viewer', password='fixture-viewer-password')
        _, viewer_session, _ = self.request(mobile_api.ENDPOINTS['session'], cookie=viewer)
        self.assertFalse(self.request(endpoint[:-7], cookie=viewer)[1]['can_delete'])
        for flags in (['A'], ['G']):
            self.auth['delegated_users'][0]['router_flags'] = {'allowed-router': flags}
            self.assertEqual(self.request(endpoint, {'note_id': 'old-note'}, cookie=viewer,
                                         headers={'X-Mobile-CSRF': viewer_session['csrf_token']})[0], 403)
        cookie = self.cookie()
        _, session, _ = self.request(mobile_api.ENDPOINTS['session'], cookie=cookie)
        headers = {'X-Mobile-CSRF': session['csrf_token']}
        for denied in ({}, {'X-Mobile-CSRF': 'wrong'}, {**headers, 'Origin': 'https://other.invalid'}):
            self.assertEqual(self.request(endpoint, {'note_id': 'old-note'}, cookie=cookie, headers=denied)[0], 403)
        for payload in ({}, [], {'note_id': '../old-note'}, {'note_id': 1}, {'note_id': 'x' * 65}):
            self.assertEqual(self.request(endpoint, payload, cookie=cookie, headers=headers)[0], 400)
        self.assertEqual(self.request(endpoint, raw=b'not JSON', method='POST', cookie=cookie,
                                     headers={**headers, 'Content-Type': 'text/plain'})[0], 400)
        self.assertEqual(self.request(endpoint.replace('allowed-router', 'missing-router'), {'note_id': 'old-note'}, cookie=cookie, headers=headers)[0], 404)
        self.assertEqual(len(self.request(endpoint[:-7], cookie=cookie)[1]['notes_entries']), 1)

    def test_legacy_note_ids_are_stable_and_can_be_deleted(self):
        self.notes_database()
        endpoint = mobile_api.BASE + '/router/allowed-router/notes'
        cookie = self.cookie()
        _, session, _ = self.request(mobile_api.ENDPOINTS['session'], cookie=cookie)
        for raw in ('Legacy plain text', '[{"text":"Legacy JSON without id"}]'):
            with self.server.app.conn() as conn:
                conn.execute("update routers set notes=? where id='allowed-router'", (raw,))
                conn.commit()
            note_id = self.request(endpoint, cookie=cookie)[1]['notes_entries'][0]['id']
            self.assertEqual(note_id, self.request(endpoint, cookie=cookie)[1]['notes_entries'][0]['id'])
            status, data, _ = self.request(endpoint + '/delete', {'note_id': note_id}, cookie=cookie,
                                          headers={'X-Mobile-CSRF': session['csrf_token']})
            self.assertEqual((status, data['notes_entries']), (200, []))

    def test_public_capabilities_and_challenge_do_not_expose_auth_secrets(self):
        status, data, headers = self.request(mobile_api.BASE + "/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(data["api_version"], 1)
        self.assertEqual(data["hub_version"], hub.HUB_LOADED_VERSION)
        self.assertTrue(data["native_password_login"])
        self.assertNotIn("fixture-owner", json.dumps(data))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        captcha = self.challenge()
        self.assertEqual(captcha["mode"], "digits")
        self.assertTrue(hub.verify_captcha(captcha["token"], captcha["code"]))
        self.assertEqual(captcha["ttl_seconds"], hub.CAPTCHA_TTL_SECONDS)
        self.assertFalse(self.sessions_file.exists())

    def test_password_login_uses_existing_secure_httponly_cookie_and_json_session(self):
        status, data, headers = self.login(headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(status, 200)
        self.assertEqual(data["username"], "fixture-owner")
        self.assertEqual(data["auth_method"], "password")
        cookie = headers["Set-Cookie"]
        for flag in ("HttpOnly", "SameSite=Lax", "Secure", "Path=/"):
            self.assertIn(flag, cookie)
        token = cookie.split(";", 1)[0]
        self.assertNotIn(token.split("=", 1)[1], json.dumps(data))
        status, data, _ = self.request(mobile_api.ENDPOINTS["session"], cookie=token)
        self.assertEqual(status, 200)
        self.assertTrue(data["account"]["is_owner"])
        self.assertEqual(data["csrf_header"], "X-Mobile-CSRF")
        self.assertEqual(len(data["csrf_token"]), 64)
        self.assertEqual(data["csrf"], {"xray_control": "fixture-xray-csrf", "hub_update": "fixture-hub-update-csrf",
                                       "router_updates": "fixture-router-updates-csrf"})
        self.assertIn("notifications", data["links"])
        self.assertEqual(set(data["session"]), {"id", "expires_at", "auth_method"})
        self.assertNotIn("token_hash", json.dumps(data))
        self.assertNotIn("password", data["auth"])

    def test_captcha_failure_stops_before_password_verification_and_expired_captcha_fails(self):
        captcha = self.challenge()
        payload = {"username": "fixture-owner", "password": "fixture-owner-password",
                   "captcha_token": captcha["token"], "captcha_answer": "wrong"}
        with mock.patch.object(hub, "verify_password_login", wraps=hub.verify_password_login) as verifier:
            status, data, _ = self.request(mobile_api.ENDPOINTS["login"], payload)
            self.assertEqual((status, data["code"]), (401, "captcha_failed"))
            verifier.assert_not_called()
        payload["captcha_answer"] = captcha["code"]
        timestamp = hub.now_ts() + hub.CAPTCHA_TTL_SECONDS + 2
        with mock.patch.object(hub, "now_ts", return_value=timestamp):
            self.assertEqual(self.request(mobile_api.ENDPOINTS["login"], payload)[0], 401)
        self.assertFalse(self.sessions_file.exists())

    def test_password_and_owner_totp_are_verified_by_existing_hub_helper(self):
        self.assertEqual(self.login(password="incorrect-password")[0], 401)
        self.auth["totp"] = {**hub.default_totp_state(), "enabled": True, "secret": "JBSWY3DPEHPK3PXP"}
        self.assertEqual(self.login()[0], 401)
        self.assertFalse(self.sessions_file.exists())
        otp = hub.totp_token(self.auth["totp"]["secret"], hub.now_ts() // hub.TOTP_PERIOD_SECONDS)
        status, data, _ = self.login(otp=otp)
        self.assertEqual(status, 200)
        self.assertEqual(data["auth_method"], "password+totp")

    def test_delegated_login_keeps_account_scope_and_hides_owner_inventory(self):
        self.auth["totp"] = {**hub.default_totp_state(), "enabled": True, "secret": "JBSWY3DPEHPK3PXP"}
        cookie = self.cookie(username="fixture-viewer", password="fixture-viewer-password")
        public = hub.public_auth_meta(self.auth)
        public.update({"ssh_keys": [{"id": "owner-key-id", "label": "owner-key-label"}], "unexpected_secret": "never-serialize"})
        with mock.patch.object(hub, "public_auth_meta", return_value=public):
            status, data, _ = self.request(mobile_api.ENDPOINTS["session"], cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(data["account"]["username"], "fixture-viewer")
        self.assertFalse(data["account"]["is_owner"])
        self.assertFalse(data["account"]["can_add_router"])
        self.assertEqual(data["account"]["router_ids"], ["allowed-router"])
        self.assertEqual(data["account"]["router_flags"], {"allowed-router": ["A"]})
        self.assertEqual(data["auth"]["username"], "fixture-viewer")
        self.assertFalse(data["auth"]["totp_enabled"])
        self.assertEqual(data["csrf"], {})
        for field in ("fixture-owner", "owner-key-id", "owner-key-label", "never-serialize", "ssh_key_count"):
            self.assertNotIn(field, json.dumps(data))
        for field in ("notifications", "hub_update", "router_updates", "vps_terminal", "router_add"):
            self.assertNotIn(field, data["links"])
        self.auth["delegated_users"][0]["enabled"] = False
        self.assertEqual(self.request(mobile_api.ENDPOINTS["session"], cookie=cookie)[0], 401)

    def test_router_manager_receives_only_router_csrf_without_owner_capabilities(self):
        self.auth["delegated_users"][0]["router_flags"] = {"allowed-router": ["G"]}
        cookie = self.cookie(username="fixture-viewer", password="fixture-viewer-password")
        _, data, _ = self.request(mobile_api.ENDPOINTS["session"], cookie=cookie)
        self.assertEqual(data["csrf"], {"xray_control": "fixture-xray-csrf"})
        self.assertEqual(data["csrf_headers"], {"xray_control": "X-Xray-Control-CSRF"})
        self.assertTrue(data["account"]["can_add_router"])
        self.assertNotIn("hub_update", data["links"])
        self.assertNotIn("router_updates", data["links"])

    def test_session_rejects_unauthenticated_agent_and_legacy_global_cookie(self):
        for kwargs in ({}, {"headers": {"Authorization": "Bearer fixture-agent"}},
                       {"cookie": hub.SESSION_COOKIE + "=fixture-legacy-cookie"}):
            status, data, _ = self.request(mobile_api.ENDPOINTS["session"], **kwargs)
            self.assertEqual((status, data["code"]), (401, "not_authenticated"))

    def test_native_password_change_negotiates_json_and_revokes_cookie(self):
        cookie = self.cookie()
        with mock.patch.object(hub, "save_auth_state") as save:
            status, data, headers = self.request("/api/auth", {"current_password": "fixture-owner-password",
                "password": "fixture-new-password", "password_confirm": "fixture-new-password"},
                cookie=cookie, headers={"Accept": "application/json"})
        self.assertEqual(status, 200)
        self.assertTrue(data["reauth_required"])
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(headers["X-Hub-Reauth"], "1")
        save.assert_called_once()
        self.assertEqual(self.request(mobile_api.ENDPOINTS["session"], cookie=cookie)[0], 401)

    def test_browser_password_change_keeps_legacy_text_response(self):
        cookie = self.cookie()
        payload = json.dumps({"current_password": "fixture-owner-password", "password": "fixture-new-password",
                              "password_confirm": "fixture-new-password"}).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            with mock.patch.object(hub, "save_auth_state"):
                conn.request("POST", "/api/auth", payload, {"Cookie": cookie, "Accept": "*/*", "Content-Type": "application/json"})
                response = conn.getresponse()
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertTrue(response.getheader("Content-Type").startswith("text/plain"))
            self.assertEqual(body, "Доступ к Hub обновлен")
            self.assertIn("Max-Age=0", response.getheader("Set-Cookie"))
        finally:
            conn.close()

    def test_recaptcha_modes_require_browser_and_never_accept_digits_as_substitute(self):
        for mode in hub.CAPTCHA_RECAPTCHA_MODES:
            with self.subTest(mode=mode):
                self.auth["captcha"] = {"mode": mode, "site_key": "fixture-site", "secret_key": "fixture-secret"}
                status, data, _ = self.request(mobile_api.BASE + "/capabilities")
                self.assertFalse(data["native_password_login"])
                status, data, _ = self.request(mobile_api.ENDPOINTS["challenge"])
                self.assertEqual(data["captcha"], {"mode": mode})
                self.assertTrue(data["browser_login_required"])
                self.assertNotIn("fixture-secret", json.dumps(data))
                with mock.patch.object(hub, "verify_password_login", wraps=hub.verify_password_login) as verifier:
                    status, data, _ = self.login()
                    self.assertEqual((status, data["code"]), (409, "browser_login_required"))
                    verifier.assert_not_called()

    def test_logout_requires_matching_session_csrf_and_revokes_only_current_session(self):
        cookie = self.cookie()
        other_cookie = self.cookie()
        _, data, _ = self.request(mobile_api.ENDPOINTS["session"], cookie=cookie)
        _, other, _ = self.request(mobile_api.ENDPOINTS["session"], cookie=other_cookie)
        self.assertNotEqual(data["csrf_token"], other["csrf_token"])
        for value in ("", "wrong", other["csrf_token"], "é"):
            status, error, _ = self.request(mobile_api.ENDPOINTS["logout"], {}, cookie=cookie,
                                            headers={"X-Mobile-CSRF": value})
            self.assertEqual((status, error["code"]), (403, "csrf_failed"))
        status, _, _ = self.request(mobile_api.ENDPOINTS["logout"], {}, cookie=cookie,
                                    headers={"X-Mobile-CSRF": data["csrf_token"], "Origin": "https://other.invalid"})
        self.assertEqual(status, 403)
        status, result, headers = self.request(mobile_api.ENDPOINTS["logout"], {}, cookie=cookie,
                                               headers={"X-Mobile-CSRF": data["csrf_token"]})
        self.assertEqual((status, result), (200, {"ok": True}))
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.request(mobile_api.ENDPOINTS["session"], cookie=cookie)[0], 401)
        self.assertEqual(self.request(mobile_api.ENDPOINTS["session"], cookie=other_cookie)[0], 200)

    def test_login_rejects_cross_origin_malformed_oversized_and_non_string_fields(self):
        self.assertEqual(self.login(headers={"Origin": "https://other.invalid"})[0], 403)
        for raw in (b"[1]", b"null", b"{", b'"text"', b'\xff', b"{}" * 5000):
            with self.subTest(raw=raw[:20]):
                status, data, _ = self.request(mobile_api.ENDPOINTS["login"], raw=raw,
                                               headers={"Content-Type": "application/json"})
                self.assertEqual((status, data["code"]), (400, "invalid_request"))
        for field in ("username", "password", "otp", "captcha_token", "captcha_answer"):
            with self.subTest(field=field):
                self.assertEqual(self.request(mobile_api.ENDPOINTS["login"], {field: {"bad": "type"}})[0], 400)
        self.assertEqual(self.request(mobile_api.ENDPOINTS["login"], raw=b"username=value",
                                      headers={"Content-Type": "application/x-www-form-urlencoded"})[0], 400)
        self.assertFalse(self.sessions_file.exists())

    def test_methods_and_unknown_paths_are_json_errors_without_touching_other_routes(self):
        self.assertEqual(self.request(mobile_api.ENDPOINTS["login"])[0], 405)
        self.assertEqual(self.request(mobile_api.ENDPOINTS["session"], {})[0], 405)
        self.assertEqual(self.request(mobile_api.BASE + "/missing")[0], 404)
        self.assertFalse(mobile_api.dispatch_get(None, "/api/routers"))
        self.assertFalse(mobile_api.dispatch_post(None, "/api/mobile/v11/login"))


class MobileUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=STATE.name)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "hub.py"
        self.api = self.root / "mobile_api.py"
        self.directory = self.root / "update"
        self.directory.mkdir()
        self.source = (ROOT / "vps/owrt-remote-hub.py").read_bytes()
        self.new = self.source + b"\n# mobile update fixture\n"
        self.target.write_bytes(self.source)
        self.api.write_bytes(b"PREVIOUS_API = True\n")
        for patcher in (
            mock.patch.object(hub, "hub_update_lock", side_effect=lambda *args, **kwargs: nullcontext()),
            mock.patch.object(hub, "hub_install_log_maintenance"),
            mock.patch.object(hub, "hub_rollback_log_maintenance"),
            mock.patch.object(hub, "hub_prune_log_maintenance_backups"),
            mock.patch.object(hub.time, "sleep"),
            mock.patch.object(hub.subprocess, "run"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.job = {"id": "c" * 32, "status": "starting", "started_at": hub.time.time(),
                    "revision": hashlib.sha256(self.new).hexdigest(),
                    "previous_revision": hashlib.sha256(self.source).hexdigest(), "health_url": "http://unused/health"}
        hub.hub_update_state(self.directory, self.job)
        self.directory.joinpath("candidate.py").write_bytes(self.new)

    def test_update_preserves_newer_native_features_in_confirmed_candidate(self):
        legacy = (ROOT / 'vps/mobile_api.py').read_bytes().replace(('MOBILE_API_REVISION = ' + str(mobile_api.MOBILE_API_REVISION)).encode(), b'MOBILE_API_REVISION = 1')
        bundle = base64.b64encode(zlib.compress(legacy, 9)).decode()
        source = (ROOT / 'vps/owrt-remote-hub.py').read_bytes()
        lines=source.splitlines(keepends=True)
        import ast
        for node in reversed(ast.parse(source).body):
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
                name=node.targets[0].id
                if name in {'HUB_MOBILE_API_SHA256','HUB_MOBILE_API_BUNDLE'}:
                    value=hashlib.sha256(legacy).hexdigest() if name.endswith('SHA256') else bundle
                    lines[node.lineno-1:node.end_lineno]=[(name+' = '+repr(value)+'\n').encode()]
        remote=b''.join(lines)
        effective=hub.hub_preserve_mobile_api(remote)
        self.assertNotEqual(remote,effective)
        self.assertEqual(hub.hub_mobile_api_payload(effective)[0], (ROOT / 'vps/mobile_api.py').read_bytes())
        self.assertEqual(hub.hub_preserve_mobile_api(effective),effective)
        hub.hub_update_validate(effective)

    def test_embedded_module_matches_source_and_tampering_is_rejected(self):
        data, digest = hub.hub_mobile_api_payload(self.source)
        self.assertEqual(data, (ROOT / "vps/mobile_api.py").read_bytes().replace(b"\r\n", b"\n"))
        self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        with self.assertRaises(ValueError):
            hub.hub_mobile_api_payload(self.source.replace(digest.encode(), b"0" * 64, 1))

    def test_update_installs_confirmed_helper_and_saves_previous_helper(self):
        with mock.patch.object(hub, "hub_update_health", return_value=True):
            self.assertEqual(hub.run_hub_update_worker(self.job["id"], self.directory, self.target), 0)
        self.assertEqual(self.api.read_bytes(), hub.hub_mobile_api_payload(self.new)[0])
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assertEqual(self.directory.joinpath("previous-mobile-api.py").read_bytes(), b"PREVIOUS_API = True\n")

    def test_failed_update_restores_existing_or_missing_helper_with_previous_hub(self):
        for present in (True, False):
            with self.subTest(present=present):
                if present:
                    self.api.write_bytes(b"PREVIOUS_API = True\n")
                else:
                    self.api.unlink(missing_ok=True)
                self.directory.joinpath("candidate.py").write_bytes(self.new)
                hub.hub_update_state(self.directory, self.job)
                with mock.patch.object(hub, "hub_update_health", side_effect=[False, True]):
                    self.assertEqual(hub.run_hub_update_worker(self.job["id"], self.directory, self.target), 1)
                self.assertEqual(self.target.read_bytes(), self.source)
                self.assertEqual(self.api.read_bytes() if self.api.exists() else None,
                                 b"PREVIOUS_API = True\n" if present else None)

    def test_failure_after_helper_replace_restores_both_files(self):
        write = hub.hub_update_write
        def fail_main(path, data, mode=0o600):
            if Path(path) == self.target and data == self.new:
                raise OSError("fixture disk full")
            return write(path, data, mode)
        with mock.patch.object(hub, "hub_update_write", side_effect=fail_main), \
                mock.patch.object(hub, "hub_update_health", return_value=True):
            self.assertEqual(hub.run_hub_update_worker(self.job["id"], self.directory, self.target), 1)
        self.assertEqual(self.target.read_bytes(), self.source)
        self.assertEqual(self.api.read_bytes(), b"PREVIOUS_API = True\n")

    def test_startup_bridges_old_worker_with_missing_helper_and_readonly_fallback(self):
        self.api.unlink()
        with mock.patch.dict(sys.modules, {"mobile_api": mobile_api}), mock.patch.object(hub, "mobile_api", None):
            digest = hub.hub_prepare_mobile_api(self.api)
            self.assertEqual(digest, hub.HUB_MOBILE_API_SHA256)
            self.assertTrue(callable(hub.mobile_api.dispatch_get))
            self.assertEqual(self.api.read_bytes(), hub.hub_mobile_api_payload()[0])
        self.api.write_bytes(b"OLD_API = True\n")
        with mock.patch.dict(sys.modules, {"mobile_api": mobile_api}), mock.patch.object(hub, "mobile_api", None), \
                mock.patch.object(hub, "hub_update_write", side_effect=PermissionError("fixture read-only directory")):
            hub.hub_prepare_mobile_api(self.api)
            self.assertTrue(callable(hub.mobile_api.dispatch_post))
        self.assertEqual(self.api.read_bytes(), b"OLD_API = True\n")


SH = shutil.which("sh")
if not SH and os.name == "nt":
    candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/sh.exe"
    SH = str(candidate) if candidate.exists() else None


@unittest.skipUnless(SH, "POSIX shell required for disposable deployment fixture")
class MobileDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owrt-native-deploy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target_hub = self.root / "installed-hub.py"
        self.target_api = self.root / "installed-api.py"
        self.target_hub.write_text("OLD_HUB = True\n", encoding="utf-8")
        self.before = self.target_hub.read_bytes()
        source = (ROOT / "vps/deploy-native-mobile-api.ps1").read_text(encoding="utf-8")
        self.script = source.split("$RemoteScript = @'\n", 1)[1].split("\n'@", 1)[0]
        self.script = self.script.replace("backup=/root/__BACKUP_NAME__",
                                          "backup=" + shlex.quote((self.root / "backup").as_posix()))
        self.script = self.script.replace("echo /opt/owrt-remote/owrt-remote-hub.py", "echo " + shlex.quote(self.target_hub.as_posix()))
        self.script = self.script.replace("echo /opt/owrt-remote/mobile_api.py", "echo " + shlex.quote(self.target_api.as_posix()))
        self.script = self.script.replace("python_bin=/opt/owrt-remote/venv/bin/python\n[ -x \"$python_bin\" ] || python_bin=python3",
                                          "python_bin=fixture_python")
        self.env = {**os.environ, "FIXTURE_PYTHON": sys.executable,
                    "FIXTURE_RESTART": (self.root / "restarted").as_posix()}
        if os.name == "nt":
            self.env["PATH"] = str(Path(SH).parent) + os.pathsep + self.env["PATH"]
            # Git's POSIX tools cannot apply Unix directory modes to every
            # Windows temporary-directory ACL. Production keeps mode 0700.
            self.script = self.script.replace("mkdir -m 700", "mkdir")
        self.prologue = r'''
fixture_python() {
    if [ "$#" = 3 ] && [ "$1" = - ]; then
        cat >/dev/null
        [ "${FAIL_HEALTH:-0}" != 1 ] || return 1
        echo 'Fixture health verified'
    else
        "$FIXTURE_PYTHON" "$@"
    fi
}
systemctl() {
    if [ "$1" = restart ] && [ "${FAIL_RESTART:-0}" = 1 ] && [ ! -e "$FIXTURE_RESTART" ]; then
        : >"$FIXTURE_RESTART"
        return 1
    fi
    return 0
}
journalctl() { :; }
sleep() { :; }
'''
        if os.name == "nt":
            self.prologue += "\nchmod() { :; }\n"

    def deploy(self, action="Install", *, bad=False, restart_fail=False, health_fail=False):
        stage = self.root / "stage"
        stage.mkdir()
        text = ("# import mobile_api\n# mobile_api.dispatch_get(self, path)\n"
                "# mobile_api.dispatch_post(self, path)\nNEW_HUB = True\n")
        stage.joinpath("hub.py").write_text(text, encoding="utf-8")
        api = "if invalid syntax\n" if bad else "def dispatch_get(handler, path): pass\ndef dispatch_post(handler, path): pass\n"
        stage.joinpath("mobile_api.py").write_text(api, encoding="utf-8")
        script = self.script.replace("stage=/tmp/__UPLOAD_NAME__", "stage=" + shlex.quote(stage.as_posix()))
        script = script.replace("__UPLOAD_NAME__", "fixture").replace("__ACTION__", action)
        result = subprocess.run([SH], input=self.prologue + script, text=True, encoding="utf-8", capture_output=True,
                                timeout=30, env={**self.env, "FAIL_RESTART": str(int(restart_fail)),
                                                 "FAIL_HEALTH": str(int(health_fail))})
        self.assertFalse(stage.exists(), result.stderr)
        self.assertFalse(list(self.root.glob("*.new-fixture")))
        return result

    def assert_original(self, api=None):
        self.assertEqual(self.target_hub.read_bytes(), self.before)
        if api is None:
            self.assertFalse(self.target_api.exists())
        else:
            self.assertEqual(self.target_api.read_bytes(), api)

    def test_install_reinstall_and_rollback_restore_original_absent_helper(self):
        for _ in range(2):
            result = self.deploy()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("NATIVE_MOBILE_API_Install_OK", result.stdout)
        result = self.deploy("Rollback")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NATIVE_MOBILE_API_Rollback_OK", result.stdout)
        self.assert_original()

    def test_rollback_restores_original_existing_helper(self):
        old_api = b"OLD_API = True\n"
        self.target_api.write_bytes(old_api)
        self.assertEqual(self.deploy().returncode, 0)
        self.assertEqual(self.deploy("Rollback").returncode, 0)
        self.assert_original(old_api)

    def test_invalid_module_or_missing_backup_changes_neither_file(self):
        self.assertNotEqual(self.deploy(bad=True).returncode, 0)
        self.assert_original()
        self.assertNotEqual(self.deploy("Rollback").returncode, 0)
        self.assert_original()

    def test_restart_or_health_failure_restores_both_files(self):
        for kwargs in ({"restart_fail": True}, {"health_fail": True}):
            with self.subTest(kwargs=kwargs):
                result = self.deploy(**kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Restoring both files", result.stderr)
                self.assert_original()

    def test_installer_extracts_exact_helper_and_preserves_previous_file_on_bad_checksum(self):
        installer = (ROOT / "vps/install-vps.sh").read_text(encoding="utf-8")
        extraction = installer.split(" <<'PY'\n", 1)[1].split("\nPY", 1)[0]
        source = self.root / "downloaded-hub.py"
        source.write_bytes((ROOT / "vps/owrt-remote-hub.py").read_bytes())
        result = subprocess.run([sys.executable, "-", str(source), str(self.target_api)], input=extraction,
                                text=True, encoding="utf-8", capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = (ROOT / "vps/mobile_api.py").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(self.target_api.read_bytes(), expected)
        source.write_bytes(source.read_bytes().replace(hub.HUB_MOBILE_API_SHA256.encode(), b"0" * 64, 1))
        result = subprocess.run([sys.executable, "-", str(source), str(self.target_api)], input=extraction,
                                text=True, encoding="utf-8", capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target_api.read_bytes(), expected)

    @unittest.skipUnless(os.name == "nt", "PowerShell staging check requires Windows")
    def test_powershell_prepare_stages_both_payloads_and_valid_shell_without_remote_access(self):
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 str(ROOT / "vps/deploy-native-mobile-api.ps1"), "-PrepareOnly"],
                                text=True, encoding="utf-8", capture_output=True, timeout=30,
                                env={**os.environ, "TEMP": str(self.root), "TMP": str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        folder = next(self.root.glob("owrt-native-mobile-api-*"))
        self.assertEqual(folder.joinpath("hub.py").read_bytes(),
                         (ROOT / "vps/owrt-remote-hub.py").read_bytes().replace(b"\r\n", b"\n"))
        self.assertEqual(folder.joinpath("mobile_api.py").read_bytes(),
                         (ROOT / "vps/mobile_api.py").read_bytes().replace(b"\r\n", b"\n"))
        check = subprocess.run([SH, "-n", str(folder / "run.sh")], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)

    @unittest.skipUnless(os.name == "nt", "PowerShell staging check requires Windows")
    def test_compatibility_deploy_uses_its_own_backup_for_install_and_rollback(self):
        for action in ("Install", "Rollback"):
            with self.subTest(action=action):
                before = set(self.root.glob("owrt-native-mobile-api-*"))
                result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                         str(ROOT / "vps/deploy-mobile-update-check.ps1"), "-Action", action, "-PrepareOnly"],
                                        text=True, encoding="utf-8", capture_output=True, timeout=30,
                                        env={**os.environ, "TEMP": str(self.root), "TMP": str(self.root)})
                self.assertEqual(result.returncode, 0, result.stderr)
                folder, = set(self.root.glob("owrt-native-mobile-api-*")) - before
                script = folder.joinpath("run.sh").read_text(encoding="utf-8")
                self.assertIn("backup=/root/owrt-remote-backup-before-mobile-update-check-v123\n", script)
                self.assertIn("action=" + action + "\n", script)
                self.assertNotIn("__BACKUP_NAME__", script)
                check = subprocess.run([SH, "-n", str(folder / "run.sh")], capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stderr)


if __name__ == "__main__":
    unittest.main()
