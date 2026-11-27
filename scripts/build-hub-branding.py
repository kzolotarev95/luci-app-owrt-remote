#!/usr/bin/env python3
"""Build the dashboard's circle/lightning mark for web, iOS and Android, offline."""
import argparse
import base64
import io
from pathlib import Path
import re
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
LIME, INK = '#c5ed34', '#0d0e10'
BOLT = [(8, 14), (12, 5), (12, 11), (16, 11), (12, 19), (12, 14)]
SVG = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108">
<rect width="108" height="108" rx="30" fill="{LIME}"/>
<g transform="translate(18.5625 18.5625) scale(2.953125)" fill="none" stroke="{INK}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
<circle cx="12" cy="12" r="9"/><path d="M8 14L12 5V11H16L12 19V14Z"/>
</g></svg>\n'''


def bitmap(size, rounded=True):
    scale = 4
    image = Image.new('RGBA', (size * scale, size * scale))
    draw = ImageDraw.Draw(image)
    side = size * scale
    draw.rounded_rectangle((0, 0, side - 1, side - 1), radius=side * 30 / 108 if rounded else 0, fill=LIME)
    def point(x, y):
        return ((18.5625 + x * 2.953125) * side / 108, (18.5625 + y * 2.953125) * side / 108)
    stroke = max(1, round(1.7 * 2.953125 * side / 108))
    draw.ellipse((*point(3, 3), *point(21, 21)), outline=INK, width=stroke)
    vertices = [point(x, y) for x, y in BOLT]
    draw.line(vertices + [vertices[0]], fill=INK, width=stroke, joint='curve')
    for x, y in vertices:
        r = stroke / 2
        draw.ellipse((x-r, y-r, x+r, y+r), fill=INK)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    outputs = {ROOT / 'vps/static/hub-logo.svg': SVG.encode()}
    assets = {}
    for name, size in [('favicon-32.png', 32), ('favicon-192.png', 192), ('favicon-512.png', 512), ('apple-touch-icon.png', 180)]:
        data = io.BytesIO()
        bitmap(size, rounded=name != 'apple-touch-icon.png').save(data, format='PNG', optimize=True)
        assets[name] = data.getvalue()
    data = io.BytesIO()
    bitmap(256).save(data, format='ICO', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    assets['favicon.ico'] = data.getvalue()
    for name, data in assets.items():
        outputs[ROOT / 'vps/static' / name] = data
    encoded = {name: base64.b64encode(data).decode() for name, data in assets.items()}
    hub = ROOT / 'vps/owrt-remote-hub.py'
    text = hub.read_text('utf-8')
    block = '# BEGIN SHARED HUB BRAND\nHUB_BRAND_SVG = ' + repr(SVG) + '\nHUB_BRAND_ASSETS = ' + repr(encoded) + '\n# END SHARED HUB BRAND'
    if '# BEGIN SHARED HUB BRAND' in text:
        text = re.sub(r'# BEGIN SHARED HUB BRAND.*?# END SHARED HUB BRAND', lambda _: block, text, flags=re.S)
    else:
        text = text.replace('def favicon_svg():', block + '\n\n\ndef favicon_svg():', 1)
    outputs[hub] = text.encode()
    router = ROOT / 'files/www/cgi-bin/owrt-remote'
    text = router.read_text('utf-8')
    favicon = base64.b64encode(SVG.encode()).decode()
    tags = f'''<!-- BEGIN SHARED HUB BRAND ICONS -->
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,{favicon}">
<link rel="apple-touch-icon" sizes="180x180" href="data:image/png;base64,{encoded['apple-touch-icon.png']}">
<meta name="apple-mobile-web-app-title" content="OWRT Hub">
<!-- END SHARED HUB BRAND ICONS -->'''
    if '<!-- BEGIN SHARED HUB BRAND ICONS -->' in text:
        text = re.sub(r'<!-- BEGIN SHARED HUB BRAND ICONS -->.*?<!-- END SHARED HUB BRAND ICONS -->', lambda _: tags, text, flags=re.S)
    else:
        text = text.replace('<meta name="viewport" content="width=device-width, initial-scale=1">', '<meta name="viewport" content="width=device-width, initial-scale=1">\n' + tags)
    outputs[router] = text.encode()
    # Android legacy logo, adaptive foreground and monochrome notification mark.
    vector = '''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">
{background}<group android:translateX="18.5625" android:translateY="18.5625" android:scaleX="2.953125" android:scaleY="2.953125">
<path android:fillColor="#00000000" android:strokeColor="{ink}" android:strokeWidth="1.7" android:strokeLineCap="round" android:strokeLineJoin="round" android:pathData="M21,12A9,9 0,1 0,3 12A9,9 0,1 0,21 12M8,14L12,5V11H16L12,19V14Z"/>
</group></vector>\n'''
    directory = ROOT / 'android/app/src/main/res/drawable'
    outputs[directory / 'ic_launcher.xml'] = vector.format(background=f'<path android:fillColor="{LIME}" android:pathData="M30,0H78Q108,0 108,30V78Q108,108 78,108H30Q0,108 0,78V30Q0,0 30,0Z"/>', ink=INK).encode()
    outputs[directory / 'ic_launcher_foreground.xml'] = vector.format(background='', ink=INK).encode()
    outputs[directory / 'ic_notification.xml'] = vector.format(background='', ink='#FFFFFF').replace('108dp', '24dp').encode()
    for path, data in outputs.items():
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                raise SystemExit('Brand asset is stale: ' + str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    print('Shared Hub brand:', len(outputs), 'files', 'checked' if args.check else 'updated')


if __name__ == '__main__':
    main()
