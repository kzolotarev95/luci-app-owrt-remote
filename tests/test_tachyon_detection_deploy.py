"""Reverse SSH verification must validate the actual router API, not the badge."""
from contextlib import closing, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest


class DetectionVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'hub.db'
        self.body = b'{"core_running":false}'
        owner = self
        class Backend(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(owner.body)
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.close)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('create table routers(id text,last_seen real,status_json text,tachyon_entry_port integer,deleted_at integer default 0)')
            db.execute('insert into routers(id,last_seen,status_json,tachyon_entry_port) values(?,?,?,?)', ('node-12', time.time(), json.dumps({
                'xray':'running', 'tachyon':{'mode':'standalone','available':True,'version':'2.0.0-beta.15'}}), self.server.server_port))

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)

    def verify(self, action='Install'):
        source = (Path(__file__).resolve().parents[1] / 'vps/deploy-tachyon-detection-fix.ps1').read_text(encoding='utf-8')
        code = source.split("$Verify = @'\n",1)[1].split("\n'@",1)[0]
        code = code.replace('/var/lib/owrt-remote/hub.db',self.db.as_posix()).replace('__ROUTER_ID__','node-12')
        code = code.replace('__SINCE__',str(int(time.time())-10)).replace('__ACTION__',action)
        code = code.replace('range(40)','range(1)').replace('time.sleep(2)','time.sleep(0)')
        output = io.StringIO()
        with redirect_stdout(output): exec(compile(code,'verification','exec'),{})
        return output.getvalue()

    def status(self):
        source = (Path(__file__).resolve().parents[1] / 'vps/deploy-tachyon-detection-fix.ps1').read_text(encoding='utf-8')
        code = source.split("$Status = @'\n",1)[1].split("\n'@",1)[0]
        code = code.replace('/var/lib/owrt-remote/hub.db',self.db.as_posix()).replace('__ROUTER_ID__','node-12')
        output = io.StringIO()
        try:
            with redirect_stdout(output): exec(compile(code,'status','exec'),{})
        except SystemExit as result:
            return result.code, output.getvalue()
        raise AssertionError('Status must explicitly report its return code')

    def test_standalone_route_with_stopped_core_is_still_a_working_web_api(self):
        self.assertIn('TACHYON_DETECTION_Router_Install_OK',self.verify())

    def test_luci_html_instead_of_tachyon_is_rejected(self):
        self.body = b'<html>LuCI login</html>'
        with self.assertRaisesRegex(SystemExit,'Result not confirmed'): self.verify()

    def test_wrong_api_field_type_is_rejected(self):
        self.body = b'{"core_running":"false"}'
        with self.assertRaisesRegex(SystemExit,'Result not confirmed'): self.verify()

    def test_stale_heartbeat_cannot_confirm_deployment(self):
        with closing(sqlite3.connect(self.db)) as db, db: db.execute('update routers set last_seen=0')
        with self.assertRaisesRegex(SystemExit,'Result not confirmed'): self.verify()

    def test_rollback_can_restore_legacy_detection(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('update routers set status_json=?',(json.dumps({'xray':'running','tachyon':{'mode':'luci'}}),))
        self.assertIn('TACHYON_DETECTION_Router_Rollback_OK',self.verify('Rollback'))

    def test_status_checks_the_selected_router_without_changing_database(self):
        before = self.db.read_bytes()
        rc, output = self.status()
        self.assertEqual(rc, 0, output)
        self.assertIn('TACHYON_CHECK_OK: node-12', output)
        self.assertIn('2.0.0-beta.15', output)
        self.assertEqual(self.db.read_bytes(), before)

    def test_status_rejects_luci_html_and_reports_failure(self):
        self.body = b'<html>LuCI</html>'
        rc, output = self.status()
        self.assertEqual(rc, 1, output)
        self.assertIn('TACHYON_CHECK_FAILED: node-12', output)


if __name__ == '__main__': unittest.main()
