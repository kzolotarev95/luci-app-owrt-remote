"""Static checks for the OpenWrt router effects/design panel."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
CGI = ROOT / 'files/www/cgi-bin/owrt-remote'
EFFECTS = ROOT / 'files/www/luci-static/resources/owrt_remote_effects.js'
DEPLOY = ROOT / 'vps/deploy-router-effects.ps1'


class RouterEffectsTests(unittest.TestCase):
    def test_router_page_exposes_all_effects_and_designs(self):
        page = CGI.read_text(encoding='utf-8')
        for mode in ('off', 'snow', 'rain', 'cosmos', 'embers', 'orbit', 'prism', 'pulse', 'laser'):
            self.assertIn(f'data-season-mode="{mode}"', page)
        for mode in ('default', 'winter', 'lite'):
            self.assertIn(f'data-hub-design="{mode}"', page)
        self.assertIn('data-hub-shared-effects="1"', page)
        self.assertIn('/luci-static/resources/owrt_remote_effects.js', page)
        self.assertIn('id="routerEffectsToggle"', page)
        self.assertIn('id="routerEffectsPanel" aria-label="Эффекты Hub" hidden', page)
        self.assertIn('class="routerEffectsDock"', page)
        self.assertIn('function setEffectsOpen(open)', page)
        self.assertIn('.seasonCard .hubDesignChoices{display:grid;grid-template-columns:1fr;', page)

    def test_shared_runtime_has_cursor_and_lite_controls(self):
        runtime = EFFECTS.read_text(encoding='utf-8')
        for marker in ('function initHubAppearance()', 'function seasonFxHandlePointerMove(',
                       "const SEASON_EFFECT_MODES", "const HUB_DESIGN_MODES",
                       "localStorage.getItem(HUB_DESIGN_KEY)"):
            self.assertIn(marker, runtime)
        self.assertGreater(len(runtime), 30000)

    def test_fresh_install_and_router_deploy_include_runtime(self):
        install = (ROOT / 'install.sh').read_text(encoding='utf-8')
        deploy = DEPLOY.read_text(encoding='utf-8')
        self.assertIn('install_file "www/luci-static/resources/owrt_remote_effects.js" 0644', install)
        self.assertIn('/www/luci-static/resources/owrt_remote_effects.js', deploy)
        self.assertIn('ROUTER_EFFECTS_V121_', deploy)
        self.assertIn('owrt-remote-backup-before-router-effects-v121', deploy)

    def test_shell_and_javascript_parse(self):
        sh = shutil.which('sh')
        if not sh and os.name == 'nt':
            candidate = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/usr/bin/sh.exe'
            sh = str(candidate) if candidate.exists() else None
        if sh:
            result = subprocess.run([sh, '-n', str(CGI)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
        node = shutil.which('node')
        if node:
            result = subprocess.run([node, '--check', str(EFFECTS)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
