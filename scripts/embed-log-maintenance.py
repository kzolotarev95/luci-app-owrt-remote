#!/usr/bin/env python3
"""Keep the button's offline maintenance payload identical to its source file."""
import argparse
import base64
import hashlib
from pathlib import Path
import re
import zlib

ROOT = Path(__file__).resolve().parents[1]
START = '# BEGIN EMBEDDED LOG MAINTENANCE'
END = '# END EMBEDDED LOG MAINTENANCE'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    payload = (ROOT / 'vps/log-maintenance.py').read_bytes().replace(b'\r\n', b'\n')
    encoded = base64.b64encode(zlib.compress(payload, 9)).decode('ascii')
    block = (START + '\nHUB_LOG_MAINTENANCE_SHA256 = ' + repr(hashlib.sha256(payload).hexdigest()) +
             '\nHUB_LOG_MAINTENANCE_BUNDLE = (\n' +
             ''.join('    ' + repr(encoded[i:i+100]) + '\n' for i in range(0, len(encoded), 100)) +
             ')\n' + END)
    path = ROOT / 'vps/owrt-remote-hub.py'
    source = path.read_text(encoding='utf-8')
    updated, count = re.subn(re.escape(START) + r'.*?' + re.escape(END), lambda m: block, source, flags=re.S)
    if count != 1:
        raise SystemExit('Expected one embedded maintenance block')
    if args.check:
        if updated != source:
            raise SystemExit('Maintenance bundle is stale: run python scripts/embed-log-maintenance.py')
    else:
        path.write_text(updated, encoding='utf-8', newline='\n')
    print('Embedded log maintenance: ' + hashlib.sha256(payload).hexdigest())


if __name__ == '__main__':
    main()
