"""2FA login remains enforced server-side and optional only when disabled."""
from html.parser import HTMLParser
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

STATE = tempfile.TemporaryDirectory(prefix='owrt-login-totp-')
spec = importlib.util.spec_from_file_location('login_totp_hub', Path(__file__).resolve().parents[1] / 'vps/owrt-remote-hub.py')
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {'OWRT_REMOTE_STATE_DIR': STATE.name}):
    spec.loader.exec_module(hub)


class Fields(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.fields = {}
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.fields[attrs['id']] = attrs


class LoginTotpTests(unittest.TestCase):
    def auth(self, enabled):
        return hub.normalize_auth_state({'username': 'owner', 'captcha': {'mode': 'digits'},
                                         'totp': {'enabled': enabled, 'secret': 'JBSWY3DPEHPK3PXP'}})

    def test_server_render_hides_and_disables_otp_when_2fa_is_off(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), mock.patch.object(hub, 'load_auth', return_value=self.auth(enabled)):
                fields = Fields(hub.login_html()).fields
                self.assertEqual('hidden' in fields['loginOtpCard'], not enabled)
                self.assertEqual('disabled' in fields['hubOtp'], not enabled)
                self.assertEqual('required' in fields['hubOtp'], enabled)

    def test_unconfirmed_secret_does_not_enable_login_2fa(self):
        auth = self.auth(False)
        self.assertFalse(hub.public_auth_meta(auth)['totp_enabled'])
        with mock.patch.object(hub, 'load_auth', return_value=auth):
            self.assertIn('hidden', Fields(hub.login_html()).fields['loginOtpCard'])

    def test_hiding_client_field_never_bypasses_enabled_owner_2fa(self):
        auth = self.auth(True)
        with mock.patch.object(hub, 'load_auth', return_value=auth), \
                mock.patch.object(hub, 'verify_login', return_value=True):
            self.assertFalse(hub.verify_password_login('owner', 'correct-password', '')[0])
            counter = hub.now_ts() // hub.TOTP_PERIOD_SECONDS
            code = hub.totp_token(auth['totp']['secret'], counter)
            ok, _, _, method = hub.verify_password_login('owner', 'correct-password', code)
            self.assertTrue(ok)
            self.assertEqual(method, 'password+totp')

    def test_owner_without_2fa_and_delegated_user_do_not_require_otp(self):
        for enabled, owner in [(False, True), (True, False)]:
            profile = {'username': 'owner' if owner else 'viewer', 'is_owner': owner}
            with self.subTest(enabled=enabled, owner=owner), \
                    mock.patch.object(hub, 'load_auth', return_value=self.auth(enabled)), \
                    mock.patch.object(hub, 'account_profile', return_value=profile), \
                    mock.patch.object(hub, 'verify_login', return_value=True):
                ok, _, _, method = hub.verify_password_login(profile['username'], 'correct-password', '')
                self.assertTrue(ok)
                self.assertEqual(method, 'password')


if __name__ == '__main__':
    unittest.main()
