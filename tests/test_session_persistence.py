"""Real session files and HTTP responses across reopen, expiry and concurrent traffic."""
import concurrent.futures
import http.client
import json
from pathlib import Path
import threading
import time
from unittest import mock

from tests.hub_http_fixture import HubHttpFixture, hub


class SessionPersistenceTests(HubHttpFixture):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(hub, 'SESSIONS_FILE', Path(self.temp.name) / 'sessions.json')
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(hub, 'add_notification')
        patch.start()
        self.addCleanup(patch.stop)
        self.token, self.session = hub.make_hub_session('owner', '192.0.2.1', 'iPhone Safari')

    def http(self, path, token=None, https=False):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            headers = {'User-Agent': 'iPhone Home Screen', 'Cookie': hub.IOS_APP_HINT_COOKIE + '=standalone'}
            if token:
                headers['Cookie'] += '; ' + hub.SESSION_COOKIE + '=' + token
            if https:
                headers['X-Forwarded-Proto'] = 'https'
            client.request('GET', path, headers=headers)
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def test_login_url_resumes_a_valid_session_on_a_new_connection(self):
        status, headers, _ = self.http('/login', self.token, https=True)
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], '/')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        cookie = headers['Set-Cookie']
        for value in ('HttpOnly', 'SameSite=Lax', 'Path=/', 'Secure', 'Expires=', 'Max-Age='):
            self.assertIn(value, cookie)
        self.assertIn(self.token, cookie)

    def test_resume_status_is_small_authenticated_and_does_not_expose_token(self):
        status, headers, body = self.http('/api/session/status', self.token, https=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'ok': True, 'authenticated': True})
        self.assertNotIn(self.token.encode(), body)
        self.assertIn('Expires=', headers['Set-Cookie'])
        # A fresh connection with the same persistent cookie stays authorized.
        self.assertEqual(self.http('/api/session/status', self.token)[0], 200)
        self.assertEqual(len(hub.load_sessions()), 1)

    def test_dashboard_document_renews_a_normal_session_cookie(self):
        for _ in range(2):
            status, headers, _ = self.http('/', self.token, https=True)
            self.assertEqual(status, 200)
            cookie = headers.get('Set-Cookie', '')
            for value in (self.token, 'HttpOnly', 'Secure', 'Expires=', 'Max-Age='):
                self.assertIn(value, cookie)
        self.assertEqual(len(hub.load_sessions()), 1, 'navigation renews the existing session')

    def test_manifest_identity_url_stays_stable_across_builds(self):
        with mock.patch.object(hub, 'SERVICE_WORKER_VERSION', 'build-1'):
            before = hub.branding_head_tags()
        with mock.patch.object(hub, 'SERVICE_WORKER_VERSION', 'build-2'):
            after = hub.branding_head_tags()
        for html in (before, after):
            self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', html)
            self.assertNotIn('/manifest.webmanifest?', html)
        self.assertEqual(json.loads(hub.web_manifest_json())['id'], '/')

    def test_expired_revoked_and_missing_sessions_still_require_login(self):
        self.assertEqual(self.http('/login')[0], 200)
        self.assertEqual(self.http('/api/session/status')[0], 401)
        hub.revoke_hub_session(token=self.token)
        self.assertEqual(self.http('/api/session/status', self.token)[0], 401)
        self.assertEqual(self.http('/login', self.token)[0], 200)
        expired = {**self.session, 'expires_at': hub.now_ts() - 1}
        hub.save_sessions([expired])
        self.assertEqual(self.http('/api/session/status', self.token)[0], 401)

    def test_concurrent_logins_do_not_overwrite_existing_sessions(self):
        barrier = threading.Barrier(24)
        original = hub.load_sessions

        def slow_load():
            rows = original()
            time.sleep(.005)
            return rows

        def login(index):
            barrier.wait(timeout=5)
            return hub.make_hub_session('owner', '192.0.2.1', 'client-' + str(index))[0]

        with mock.patch.object(hub, 'load_sessions', side_effect=slow_load), \
                concurrent.futures.ThreadPoolExecutor(max_workers=24) as executor:
            tokens = list(executor.map(login, range(24)))
        self.assertEqual(len(hub.load_sessions()), 25)
        for token in [self.token, *tokens]:
            self.assertIsNotNone(hub.verify_hub_session(token, touch=False))

    def test_readers_never_see_a_partially_written_session_file(self):
        started, release = threading.Event(), threading.Event()
        original = Path.write_text
        target = hub.SESSIONS_FILE

        def slow_write(path, text, *args, **kwargs):
            if path.parent == target.parent and 'token_hash' in text:
                with path.open('w', encoding='utf-8') as stream:
                    halfway = len(text) // 2
                    stream.write(text[:halfway])
                    stream.flush()
                    started.set()
                    if not release.wait(timeout=5):
                        raise RuntimeError('test writer was not released')
                    stream.write(text[halfway:])
                return len(text)
            return original(path, text, *args, **kwargs)

        next_rows = [{**self.session, 'client_hint': 'hub'}]
        with mock.patch.object(Path, 'write_text', slow_write), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            writer = executor.submit(hub.save_sessions, next_rows)
            try:
                self.assertTrue(started.wait(timeout=5))
                data = json.loads(target.read_text('utf-8'))
                self.assertEqual(len(data['sessions']), 1)
            finally:
                release.set()
                writer.result(timeout=5)
        self.assertEqual(json.loads(target.read_text('utf-8'))['sessions'], next_rows)

    def test_a_concurrent_touch_cannot_restore_a_revoked_session(self):
        writing, release, revoking = threading.Event(), threading.Event(), threading.Event()
        original = hub.save_sessions
        original([{**self.session, 'last_seen': hub.now_ts() - 120}])

        def delayed_save(rows):
            if threading.current_thread().name.startswith('touch'):
                writing.set()
                if not release.wait(timeout=5):
                    raise RuntimeError('test touch was not released')
            return original(rows)

        def revoke():
            revoking.set()
            return hub.revoke_hub_session(token=self.token)

        with mock.patch.object(hub, 'save_sessions', side_effect=delayed_save), \
                concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='touch') as touching, \
                concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='revoke') as revoker:
            touch = touching.submit(hub.verify_hub_session, self.token)
            try:
                self.assertTrue(writing.wait(timeout=5))
                revocation = revoker.submit(revoke)
                self.assertTrue(revoking.wait(timeout=5))
                try:
                    revocation.result(timeout=.1)
                except concurrent.futures.TimeoutError:
                    pass
            finally:
                release.set()
                touch.result(timeout=5)
            self.assertEqual(revocation.result(timeout=5), 1)
        self.assertIsNone(hub.verify_hub_session(self.token, touch=False))
