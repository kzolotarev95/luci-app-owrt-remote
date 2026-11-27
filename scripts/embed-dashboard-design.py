#!/usr/bin/env python3
"""Embed dashboard assets so a one-file Hub update carries its complete UI."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = '# BEGIN EMBEDDED DASHBOARD DESIGN'
END = '# END EMBEDDED DASHBOARD DESIGN'
ASSETS = (
    ('HUB_DEFAULT_PALETTE_CSS', 'hub-palette.css'),
    ('HUB_ROUTER_MODEL_PHOTOS_JS', 'router-model-photos.js'),
    ('HUB_ROUTER_MODEL_PHOTOS_CSS', 'router-model-photos.css'),
    ('HUB_DASHBOARD_VANTAGE_CSS', 'dashboard-vantage.css'),
    ('HUB_DASHBOARD_NETWORK_CSS', 'dashboard-network.css'),
    ('HUB_DASHBOARD_NETWORK_JS', 'dashboard-network.js'),
    ('HUB_DASHBOARD_VANTAGE_JS', 'dashboard-vantage.js'),
    ('HUB_ROUTER_ORDER_JS', 'router-order.js'),
    ('HUB_ROUTER_TERMINAL_COMMANDS_JS', 'router-terminal-commands.js'),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    blocks = [START]
    for name, filename in ASSETS:
        source = (ROOT / 'vps' / filename).read_text(encoding='utf-8')
        if "'''" in source or '</script' in source.lower() or '</style' in source.lower():
            raise SystemExit('Unsafe inline asset: ' + filename)
        blocks.append(name + " = r'''\n" + source.rstrip() + "\n'''\n")
    blocks.append(END)
    block = '\n'.join(blocks)
    path = ROOT / 'vps/owrt-remote-hub.py'
    source = path.read_text(encoding='utf-8')
    if START in source:
        start = source.index(START)
        end = source.index(END, start) + len(END)
        updated = source[:start] + block + source[end:]
    else:
        updated = source.replace('def hub_sidebar_icon(name):', block + '\n\n\ndef hub_sidebar_icon(name):', 1)
    if args.check:
        if source != updated:
            raise SystemExit('Dashboard assets are stale: run python scripts/embed-dashboard-design.py')
    else:
        path.write_text(updated, encoding='utf-8', newline='\n')
    router_path = ROOT / 'files/www/cgi-bin/owrt-remote'
    router_source = router_path.read_text(encoding='utf-8')
    router_css = (ROOT / 'vps/router-vantage.css').read_text(encoding='utf-8').strip()
    css_start, css_end = '/* BEGIN EMBEDDED ROUTER VANTAGE */', '/* END EMBEDDED ROUTER VANTAGE */'
    css_block = css_start + '\n' + router_css + '\n' + css_end
    if css_start in router_source:
        start = router_source.index(css_start)
        end = router_source.index(css_end, start) + len(css_end)
        router_updated = router_source[:start] + css_block + router_source[end:]
    else:
        marker = '</style>\n</head>\n<body class="routerShell">'
        assert router_source.count(marker) == 1
        router_updated = router_source.replace(marker, css_block + '\n' + marker, 1)
    if args.check:
        if router_updated != router_source:
            raise SystemExit('Router dashboard assets are stale: run python scripts/embed-dashboard-design.py')
    else:
        router_path.write_text(router_updated, encoding='utf-8', newline='\n')
    # Both router-owned pages carry the same palette; no extra HTTP request.
    palette_start, palette_end = '/* BEGIN SHARED HUB PALETTE */', '/* END SHARED HUB PALETTE */'
    palette = (ROOT / 'vps/hub-palette.css').read_text(encoding='utf-8').strip()
    palette_block = palette_start + '\n' + palette + '\n' + palette_end + '\n'
    router_source = router_updated
    if palette_start in router_source:
        import re
        router_updated = re.sub(re.escape(palette_start) + r'.*?' + re.escape(palette_end) + r'\n?',
                                lambda match: palette_block, router_source, flags=re.S)
    else:
        assert router_source.count('</style>\n</head>') == 2
        router_updated = router_source.replace('</style>\n</head>', palette_block + '</style>\n</head>')
    if args.check:
        if router_updated != router_source:
            raise SystemExit('Router palette is stale: run python scripts/embed-dashboard-design.py')
    else:
        router_path.write_text(router_updated, encoding='utf-8', newline='\n')
    print('Embedded dashboard assets: OK')


if __name__ == '__main__':
    main()
