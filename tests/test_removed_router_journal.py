"""Journal retirement must not damage router state or independent Xray control."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
import urllib.error

import hub_http_fixture as base

ROOT, hub = base.ROOT, base.hub
SH = 'C:/Program Files/Git/usr/bin/sh.exe' if os.name == 'nt' else '/bin/sh'


class RemovedJournalHttpTests(base.HubHttpFixture):
    def test_upgrade_erases_old_archive_and_keeps_routers_notes_and_control(self):
        marker = b'PRIVATE-RETIRED-JOURNAL-' * 2048
        with self.app.conn() as conn:
            conn.execute('create table router_logs(router_id text primary key, content blob)')
            conn.execute('insert into router_logs values (?, ?)', ('main', marker))
            conn.execute("update routers set notes='Keep my notes' where id='main'")
        before = self.app.db_path.stat().st_size
        self.request('/api/heartbeat', {'id':'main','xray':'stopped','xray_control':1}, cookie=False, agent=True)
        with self.app.conn() as conn:
            self.assertFalse(conn.execute("select 1 from sqlite_master where name='router_logs'").fetchone())
            self.assertEqual(conn.execute("select notes from routers where id='main'").fetchone()[0], 'Keep my notes')
            self.assertEqual(conn.execute('select count(*) from routers').fetchone()[0], 2)
            hub.xray_control_enqueue(conn, 'main', 'start', 'a' * 32)
            self.assertEqual(len(hub.xray_control_snapshot(conn, 'main')['history']), 1)
        self.assertNotIn(marker[:256], self.app.db_path.read_bytes())
        self.assertLess(self.app.db_path.stat().st_size, before)
        with self.app.conn() as conn:
            self.assertEqual(conn.execute('select count(*) from routers').fetchone()[0], 2)

    def test_old_endpoints_cannot_receive_or_return_archives(self):
        for path, payload, agent in [
            ('/api/router/main/logs', None, False),
            ('/api/router/main/logs/download', None, False),
            ('/router/main/logs', None, False),
            ('/api/router-logs', {'id':'main','batch':{'data':'secret'}}, True),
            ('/api/router/main/logs/clear', {}, False),
        ]:
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path, payload, cookie=not agent, agent=agent)
            self.assertIn(error.exception.code, (401, 404))
        raw, _ = self.request('/api/heartbeat', {'id':'main','name':'Kept router','xray':'stopped'}, cookie=False, agent=True)
        self.assertTrue(json.loads(raw)['ok'])
        with self.app.conn() as conn:
            router = hub.row_to_router(hub.get_active_router(conn, 'main'))
            self.assertNotIn('logs_url', router)
            self.assertFalse(conn.execute("select 1 from sqlite_master where name='router_logs'").fetchone())


class RetiredCollectorTests(unittest.TestCase):
    def test_cleanup_is_idempotent_and_confined_to_owned_directories(self):
        with tempfile.TemporaryDirectory(prefix='retire-remote-') as folder:
            root = Path(folder)
            old = ['tmp/owrt-remote-log-runtime', 'tmp/owrt-remote-logs',
                   'overlay/owrt-remote/logs', 'etc/owrt-remote/logs']
            keep = ['etc/config/owrtremote','etc/owrt-remote/web.key',
                    'etc/xray/owrt-remote-client.json','etc/owrt-remote/control/stopped',
                    'etc/owrt-remote/xray-cache/xray.gz','var/log/syslog']
            for relative in old:
                path = root / relative
                path.mkdir(parents=True)
                (path / 'archive').write_text('retired journal')
            for relative in keep + ['usr/lib/owrt-remote/logs.sh','etc/init.d/owrt-remote-logs']:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('Keep this exact data')
            for _ in range(2):
                result = subprocess.run([SH, str(ROOT/'files/usr/lib/owrt-remote/retire-logs.sh')],
                    env={**os.environ,'PATH':str(Path(SH).parent)+os.pathsep+os.environ.get('PATH',''),
                         'IPKG_INSTROOT':('/'+root.drive[0].lower()+root.as_posix()[2:] if os.name=='nt' else root.as_posix())}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for relative in old + ['usr/lib/owrt-remote/logs.sh','etc/init.d/owrt-remote-logs']:
                self.assertFalse((root / relative).exists(), relative)
            for relative in keep:
                self.assertEqual((root / relative).read_text(), 'Keep this exact data', relative)

    def test_fresh_payload_has_no_collector_and_no_background_logger(self):
        self.assertFalse((ROOT/'files/usr/lib/owrt-remote/logs.sh').exists())
        self.assertFalse((ROOT/'files/etc/init.d/owrt-remote-logs').exists())
        agent = (ROOT/'files/usr/sbin/owrt-remote').read_text(encoding='utf-8')
        self.assertNotIn('sync_global_logs', agent)
        self.assertNotIn('logger -t', agent)
        self.assertNotIn('/api/router-logs', agent)
        init = (ROOT/'files/etc/init.d/owrt-remote').read_text(encoding='utf-8')
        self.assertNotIn('owrt-remote-logs', init)
        self.assertNotIn('procd_set_param stderr 1', init)


if __name__ == '__main__':
    unittest.main()
