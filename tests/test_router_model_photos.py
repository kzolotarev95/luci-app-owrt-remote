"""Curated photo payload and public static HTTP behaviour. No remote downloads."""
import base64
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request

from tests.hub_http_fixture import HubHttpFixture, hub

ROOT = Path(__file__).resolve().parents[1]


class RouterModelPhotoTests(HubHttpFixture):
    def test_source_catalog_and_embedded_photos_agree(self):
        manifest = json.loads((ROOT / 'vps/router-model-photos/catalog.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['catalog_rows'], 92)
        self.assertEqual(len(manifest['models']), len(hub.HUB_ROUTER_MODEL_CATALOG))
        assets = {row['id']: row for row in hub.HUB_ROUTER_MODEL_CATALOG}
        for row in manifest['models']:
            with self.subTest(model=row['id']):
                self.assertEqual(assets[row['id']]['aliases'], row['aliases'])
                if not row['file']:
                    self.assertIsNone(assets[row['id']]['photo'])
                    continue
                body = (ROOT / 'vps/router-model-photos' / row['file']).read_bytes()
                self.assertEqual(hashlib.sha256(body).hexdigest(), row['sha256'])
                self.assertLess(len(body), 50000)
                self.assertEqual(body[:4], b'RIFF')
                self.assertEqual(body[8:12], b'WEBP')
                filename = assets[row['id']]['photo'].rsplit('/', 1)[1]
                self.assertEqual(base64.b64decode(hub.HUB_ROUTER_MODEL_PHOTO_DATA[filename]), body)
                self.assertTrue(row['source_page'].startswith('https://') or row['source_page'].startswith('http://'))

    def test_photo_is_local_cached_webp_with_conditional_get(self):
        model = next(item for item in hub.HUB_ROUTER_MODEL_CATALOG if item['id'] == 'cudy-wbr3000uax')
        body, headers = self.request(model['photo'], cookie=False)
        self.assertEqual(body[8:12], b'WEBP')
        self.assertEqual(headers['Content-Type'], 'image/webp')
        self.assertIn('immutable', headers['Cache-Control'])
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}' + model['photo'],
                                         headers={'If-None-Match': headers['ETag']})
        with self.assertRaises(urllib.error.HTTPError) as result:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(result.exception.code, 304)
        self.assertEqual(result.exception.read(), b'')

    def test_invalid_names_never_access_files_or_remote_urls(self):
        for suffix in ('missing.webp', '../hub.db', '%2e%2e%2fhub.db', 'https://example.test/router.webp'):
            with self.subTest(suffix=suffix), self.assertRaises(urllib.error.HTTPError) as result:
                self.request('/router-model-photos/' + suffix, cookie=False)
            self.assertEqual(result.exception.code, 404)
