#!/usr/bin/env python3
"""Generate the router's immutable-commit update inventory (no release build)."""
import argparse
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    'usr/sbin/owrt-remote': '0755',
    'usr/lib/owrt-remote/wan.sh': '0644',
    'usr/lib/owrt-remote/control.sh': '0644',
    'usr/lib/owrt-remote/tachyon.sh': '0644',
    'usr/lib/owrt-remote/retire-logs.sh': '0644',
    'usr/lib/owrt-remote/panel-update.sh': '0644',
    'usr/lib/owrt-remote/panel-download.sh': '0644',
    'etc/init.d/owrt-remote': '0755',
    'etc/hotplug.d/iface/99-owrt-remote': '0755',
    'www/cgi-bin/owrt-remote': '0755',
    'www/luci-static/resources/owrt_remote_effects.js': '0644',
    'www/luci-static/resources/owrt_remote_update.js': '0644',
    'www/luci-static/resources/view/owrt_remote.js': '0644',
    'usr/share/luci/menu.d/luci-app-owrt-remote.json': '0644',
    'usr/share/rpcd/acl.d/luci-app-owrt-remote.json': '0644',
    'usr/share/owrt-remote/panel-version': '0644',
}


def generate(check=False):
    recipe = (ROOT / 'package/luci-app-owrt-remote/Makefile').read_text(encoding='utf-8')
    version = re.search(r'^PKG_VERSION:=(\d+)$', recipe, re.M)[1]
    release = re.search(r'^PKG_RELEASE:=(\d+)$', recipe, re.M)[1]
    generated = {
        ROOT / 'files/usr/lib/owrt-remote/panel-download.sh':
            (ROOT / 'scripts/router-download.sh').read_text(encoding='utf-8'),
        ROOT / 'files/usr/share/owrt-remote/panel-version': version + ' ' + release + '\n',
    }
    for path, content in generated.items():
        if check:
            if not path.exists() or path.read_text(encoding='utf-8') != content:
                raise SystemExit('Stale router update asset: ' + str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8', newline='\n')
    # Same-version fixes also need a new browser/Hub-proxy cache key.
    cgi = ROOT / 'files/www/cgi-bin/owrt-remote'
    old = cgi.read_text(encoding='utf-8')
    asset = hashlib.sha256((ROOT / 'files/www/luci-static/resources/owrt_remote_update.js').read_bytes().replace(b'\r\n', b'\n')).hexdigest()[:16]
    new = re.sub(r'^OWRT_REMOTE_UPDATE_ASSET_VERSION="[^"]*"$',
                 'OWRT_REMOTE_UPDATE_ASSET_VERSION="' + asset + '"', old, flags=re.M)
    if new != old:
        if check:
            raise SystemExit('Router update JS cache key is stale: regenerate the manifest')
        cgi.write_text(new, encoding='utf-8', newline='\n')
    lines = ['OWRT_ROUTER_UPDATE 1', 'version ' + version, 'release ' + release]
    for name, mode in FILES.items():
        data = (ROOT / 'files' / name).read_bytes().replace(b'\r\n', b'\n')
        lines.append(hashlib.sha256(data).hexdigest() + ' ' + mode + ' ' + name)
    content = '\n'.join(lines) + '\n'
    path = ROOT / 'router-update.manifest'
    if check:
        if not path.exists() or path.read_text(encoding='utf-8') != content:
            raise SystemExit('Run python scripts/router-update-manifest.py before publishing router changes')
    else:
        path.write_text(content, encoding='utf-8', newline='\n')
    print('Router update manifest: OK (' + version + '-' + release + ')')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    generate(parser.parse_args().check)
