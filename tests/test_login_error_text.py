"""Failed login responses keep their Russian messages and reject the login."""
from unittest import mock
import unittest
import urllib.error

import hub_http_fixture as base


class LoginErrorTests(base.HubHttpFixture):
    def test_invalid_captcha_returns_readable_russian(self):
        with mock.patch.object(base.hub, 'verify_captcha', return_value=False):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/login', {'username':'owner','password':'wrong','captcha_answer':'0000'}, cookie=False)
        self.assertEqual(error.exception.code,401)
        page = error.exception.read().decode('utf-8')
        self.assertIn('Неверная капча', page)
        self.assertNotIn('РќРµРІРµСЂРЅР°СЏ', page)

    def test_invalid_password_is_not_reported_as_captcha_failure(self):
        with mock.patch.object(base.hub, 'verify_captcha', return_value=True), \
             mock.patch.object(base.hub, 'verify_password_login', return_value=(False,'',{},'password')):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/login', {'username':'owner','password':'wrong'}, cookie=False)
        self.assertEqual(error.exception.code,401)
        page = error.exception.read().decode('utf-8')
        self.assertIn('Неверный логин или пароль', page)
        self.assertNotIn('РќРµРІРµСЂРЅС‹Р№', page)


if __name__ == '__main__':
    unittest.main()
