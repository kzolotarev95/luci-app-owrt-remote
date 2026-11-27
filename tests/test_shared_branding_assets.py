"""Public icon delivery must update even when the server retains older static files."""
import base64
import io
import json
from pathlib import Path
from PIL import Image
from tests.hub_http_fixture import HubHttpFixture, hub

ROOT = Path(__file__).resolve().parents[1]


class SharedBrandTests(HubHttpFixture):
    def test_current_icons_are_embedded_and_served_without_reading_stale_files(self):
        expected_sizes = {'favicon-32.png': (32, 32), 'favicon-192.png': (192, 192),
                          'favicon-512.png': (512, 512), 'apple-touch-icon.png': (180, 180)}
        for filename, size in expected_sizes.items():
            with self.subTest(filename=filename):
                body, headers = self.request('/' + filename, cookie=False)
                self.assertEqual(body, base64.b64decode(hub.HUB_BRAND_ASSETS[filename]))
                self.assertEqual(body, (ROOT / 'vps/static' / filename).read_bytes())
                self.assertEqual(headers['Content-Type'], 'image/png')
                with Image.open(io.BytesIO(body)) as image:
                    self.assertEqual(image.size, size)
                    self.assertEqual(image.getpixel((size[0] // 2, 4))[:3], (197, 237, 52))
        ico, headers = self.request('/favicon.ico', cookie=False)
        self.assertEqual(ico[:4], b'\x00\x00\x01\x00')
        self.assertEqual(ico, base64.b64decode(hub.HUB_BRAND_ASSETS['favicon.ico']))

    def test_svg_and_ios_manifest_share_the_current_mark(self):
        body, _ = self.request('/favicon.svg', cookie=False)
        self.assertIn(b'M8 14L12 5V11H16L12 19V14Z', body)
        self.assertEqual(body, (ROOT / 'vps/static/hub-logo.svg').read_bytes())
        manifest = json.loads(hub.web_manifest_json())
        self.assertTrue(any(item['src'].startswith('/apple-touch-icon.png?') for item in manifest['icons']))
