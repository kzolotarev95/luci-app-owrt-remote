#!/usr/bin/env python3
"""Keep the self-contained installer and agent's downloader identical."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
start = '# BEGIN SHARED ROUTER DOWNLOADER\n'
end = '# END SHARED ROUTER DOWNLOADER\n'
block = start + (root / 'scripts/router-download.sh').read_text(encoding='utf-8').rstrip() + '\n' + end
for name in ('install.sh', 'files/usr/sbin/owrt-remote'):
    path = root / name
    text = path.read_text(encoding='utf-8')
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    updated = before + block + after
    if '--check' in sys.argv:
        if updated != text:
            raise SystemExit('Downloader out of date: ' + name)
    else:
        path.write_text(updated, encoding='utf-8', newline='\n')
print('Shared router downloader: OK')
