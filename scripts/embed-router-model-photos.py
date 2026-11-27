#!/usr/bin/env python3
"""Bundle curated router photos so Hub installation/self-update remains atomic."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import zlib

ROOT = Path(__file__).resolve().parents[1]
START = '# BEGIN EMBEDDED ROUTER MODEL PHOTOS'
END = '# END EMBEDDED ROUTER MODEL PHOTOS'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    directory = ROOT / 'vps/router-model-photos'
    manifest = json.loads((directory / 'catalog.json').read_text(encoding='utf-8'))
    catalog, images, seen = [], {}, set()
    for model in manifest['models']:
        ident = model['id']
        if not re.fullmatch(r'[a-z0-9-]{1,100}', ident) or ident in seen:
            raise SystemExit('Invalid or duplicate router model ID: ' + ident)
        seen.add(ident)
        public = {key: model[key] for key in ('id', 'model', 'aliases')}
        public['photo'] = None
        if model.get('file'):
            if model['file'] != ident + '.webp':
                raise SystemExit('Invalid photo filename: ' + ident)
            data = (directory / model['file']).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != model['sha256'] or len(data) > 50000 or data[:4] != b'RIFF' or data[8:12] != b'WEBP':
                raise SystemExit('Photo validation failed: ' + ident)
            filename = ident + '.' + digest[:12] + '.webp'
            images[filename] = base64.b64encode(data).decode('ascii')
            public['photo'] = '/router-model-photos/' + filename
        catalog.append(public)
    payload = json.dumps(images, separators=(',', ':'), sort_keys=True).encode('ascii')
    encoded = base64.b64encode(zlib.compress(payload, 9)).decode('ascii')
    block = START + '\nHUB_ROUTER_MODEL_CATALOG = ' + repr(catalog) + '\n'
    block += 'HUB_ROUTER_MODEL_PHOTO_DATA = json.loads(zlib.decompress(base64.b64decode(\n' + ''.join(
        '    ' + repr(encoded[i:i+100]) + '\n' for i in range(0, len(encoded), 100)) + ')))\n' + END
    path = ROOT / 'vps/owrt-remote-hub.py'
    source = path.read_text(encoding='utf-8')
    if START in source:
        start, end = source.index(START), source.index(END) + len(END)
        updated = source[:start] + block + source[end:]
    else:
        marker = 'def hub_sidebar_icon(name):'
        assert source.count(marker) == 1
        updated = source.replace(marker, block + '\n\n\n' + marker, 1)
    if args.check:
        if updated != source:
            raise SystemExit('Router photo bundle is stale: run python scripts/embed-router-model-photos.py')
    else:
        path.write_text(updated, encoding='utf-8', newline='\n')
    print(f'Embedded router photos: {len(images)} images, {len(catalog)} model records')


if __name__ == '__main__':
    main()
