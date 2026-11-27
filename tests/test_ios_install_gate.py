"""iOS document gate precedes login and leaves authentication/API behavior intact."""
import http.client
import json
from pathlib import Path
import sys
from unittest import mock

from tests.hub_http_fixture import HubHttpFixture, hub

IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1'


class IosInstallGateTests(HubHttpFixture):
    def http(self, path, ua=IPHONE, installed=False, authenticated=False, post=False):
        cookies = []
        if installed:
            cookies.append(hub.IOS_APP_HINT_COOKIE + '=standalone')
        if authenticated:
            cookies.append(hub.SESSION_COOKIE + '=session-test')
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            client.request('POST' if post else 'GET', path, body='username=owner&password=unused' if post else None,
                           headers={'User-Agent': ua, 'Cookie': '; '.join(cookies), 'Content-Type': 'application/x-www-form-urlencoded'})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode('utf-8', errors='replace')
        finally:
            client.close()

    def test_ios_browser_receives_installation_instead_of_login_or_dashboard(self):
        for path in ('/', '/login', '/vps-terminal/', '/router-updates/', '/router/main/notes', '/router/main/config'):
            for authenticated in (False, True):
                with self.subTest(path=path, authenticated=authenticated):
                    status, _, body = self.http(path, authenticated=authenticated)
                    self.assertEqual(status, 200)
                    self.assertIn('owrt-hub-ios-install', body)
                    self.assertIn('В браузере панель недоступна', body)
                    self.assertNotIn('passwordLoginForm', body)
                    self.assertNotIn('window.ROUTERS', body)
                    self.assertNotIn('resumeHubLoginSession', body)

    def test_old_browser_login_form_cannot_submit_before_installation(self):
        with mock.patch.object(hub.Handler, 'login') as login:
            _, _, body = self.http('/login', post=True)
        login.assert_not_called()
        self.assertIn('owrt-hub-ios-install', body)

    def test_standalone_bootstrap_can_reach_existing_authentication(self):
        status, _, body = self.http('/login', installed=True)
        self.assertEqual(status, 200)
        self.assertIn('passwordLoginForm', body)
        self.assertNotIn('owrt-hub-ios-install -->', body)
        status, headers, _ = self.http('/login', installed=True, authenticated=True)
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], '/')

    def test_pc_and_android_continue_using_the_browser(self):
        for ua in ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130', 'Mozilla/5.0 (Linux; Android 15) Chrome/130'):
            with self.subTest(ua=ua):
                _, _, body = self.http('/login', ua=ua)
                self.assertIn('passwordLoginForm', body)
                self.assertNotIn('owrt-hub-ios-install -->', body)

    def test_manifest_and_session_api_remain_available_before_installation(self):
        status, _, body = self.http('/manifest.webmanifest')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['start_url'], '/')
        self.assertEqual(self.http('/api/session/status')[0], 401)

    def test_ui_gate_does_not_replace_authentication(self):
        self.assertEqual(self.http('/api/session/status', installed=True)[0], 401)
        self.assertEqual(self.http('/api/routers', installed=True)[0], 401)

    def test_install_target_cannot_redirect_to_another_site_or_logout(self):
        for target in ('https://evil.example', '//evil.example', '/\\evil.example', '/logout', '/api/routers', '/ios-install/', '/\nlogin'):
            with self.subTest(target=target):
                self.assertEqual(hub.ios_install_target(target), '/')
        self.assertEqual(hub.ios_install_target('/router/main/notes?edit=1'), '/router/main/notes?edit=1')
        self.assertNotIn('</script><script>evil', hub.ios_install_html('/login?q=</script><script>evil'))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--export-preview':
        hub.write_json_private(hub.AUTH_FILE, hub.normalize_auth_state({'username': 'owner', 'captcha': {'mode': 'digits'}}))
        with mock.patch.object(hub, 'web_push_backend_status', return_value={'ready': False}):
            pages = {'/login': hub.login_html(), '/': hub.dashboard_html([], 'owner', auth_bootstrap={'is_owner': True}),
                     '/ios-install/': hub.ios_install_html('/login')}
        Path(sys.argv[2]).write_text(json.dumps(pages, ensure_ascii=False), encoding='utf-8')
    else:
        import unittest
        unittest.main()
