#!/usr/bin/env python3
"""Embed the exact mobile API source in Hub; --check detects a stale bundle."""
import argparse
import ast
import base64
import hashlib
from pathlib import Path
import warnings
import zlib

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    api = (ROOT / 'vps/mobile_api.py').read_bytes().replace(b'\r\n', b'\n')
    compile(api, 'mobile_api.py', 'exec', dont_inherit=True)
    if not 1000 <= len(api) <= 131072:
        raise SystemExit('Mobile API size is outside the supported bundle range')
    replacements = {
        'HUB_MOBILE_API_SHA256': hashlib.sha256(api).hexdigest(),
        'HUB_MOBILE_API_BUNDLE': base64.b64encode(zlib.compress(api, 9)).decode('ascii'),
    }
    hub = ROOT / 'vps/owrt-remote-hub.py'
    source = hub.read_text(encoding='utf-8')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', SyntaxWarning)
        tree = ast.parse(source)
    nodes = [node for node in tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1
             and isinstance(node.targets[0], ast.Name) and node.targets[0].id in replacements]
    if len(nodes) != 2 or {node.targets[0].id for node in nodes} != set(replacements):
        raise SystemExit('Hub mobile bundle constants are missing or duplicated')
    lines = source.splitlines(keepends=True)
    for node in reversed(nodes):
        name = node.targets[0].id
        if args.check and ast.literal_eval(node.value) != replacements[name]:
            raise SystemExit('Embedded mobile API is stale: run python scripts/embed-mobile-api.py')
        lines[node.lineno - 1:node.end_lineno] = [name + ' = ' + repr(replacements[name]) + '\n']
    if not args.check:
        hub.write_text(''.join(lines), encoding='utf-8', newline='\n')
    print('Embedded mobile API:', replacements['HUB_MOBILE_API_SHA256'], 'checked' if args.check else 'updated')


if __name__ == '__main__':
    main()
