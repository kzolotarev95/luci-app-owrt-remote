#!/usr/bin/env python3
"""Copy the verified Hub model-photo catalog to Android assets, without network access."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate existing assets without writing')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    source = root / 'vps/router-model-photos'
    target = root / 'android/app/src/main/assets/router-models'
    catalog = json.loads((source / 'catalog.json').read_text('utf-8'))['models']
    minimal = [{key: item.get(key) for key in ('model', 'aliases', 'file')} for item in catalog]
    files = {}
    for item in catalog:
        name = item.get('file')
        if not name:
            continue
        if not re.fullmatch(r'[a-z0-9-]+\.webp', name):
            raise SystemExit(f'Invalid asset path: {name!r}')
        data = (source / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise SystemExit(f'Source photo checksum mismatch: {name}')
        files[name] = data
    if args.check:
        actual = json.loads((target / 'catalog.json').read_text('utf-8'))
        if actual != minimal:
            raise SystemExit('Android photo catalog differs from the verified Hub catalog')
        for name, data in files.items():
            if not (target / name).is_file() or (target / name).read_bytes() != data:
                raise SystemExit(f'Android photo differs from verified source: {name}')
        extras = {p.name for p in target.glob('*.webp')} - files.keys()
        if extras:
            raise SystemExit(f'Uncatalogued Android photos: {sorted(extras)}')
    else:
        target.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (target / name).write_bytes(data)
        (target / 'catalog.json').write_text(json.dumps(minimal, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(f'Android model photos: {len(catalog)} models, {len(files)} verified images; ' + ('checked' if args.check else 'synced'))


if __name__ == '__main__':
    main()
