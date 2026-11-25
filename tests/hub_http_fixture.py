"""Loopback HTTP fixture for Hub integration tests."""
import base64
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
STATE = tempfile.TemporaryDirectory(prefix='hub-http-fixture-')
spec = importlib.util.spec_from_file_location('http_fixture_hub', ROOT / 'vps/owrt-remote-hub.py')
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {'OWRT_REMOTE_STATE_DIR': STATE.name}):
    spec.loader.exec_module(hub)


class QuietHandler(hub.Handler):
    def log_message(self, *args):
        pass


class HubHttpFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=STATE.name)
        self.addCleanup(self.temp.cleanup)
        self.app = hub.App(Path(self.temp.name) / 'hub.db', 'session-test', 'agent-test', '')
        with self.app.conn() as conn:
            hub.upsert_router(conn, {'id': 'main', 'name': 'Test router', 'vps_host': 'hub.example'})
            hub.upsert_router(conn, {'id': 'other', 'name': 'Other router', 'vps_host': 'hub.example'})
        hub.write_json_private(hub.AUTH_FILE, {'username': 'owner', 'password_hash': 'unused'})
        self.server = hub.HubHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.server.app = self.app
        self.server.is_tls = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, payload=None, cookie=True, agent=False, method=None):
        headers = {}
        if cookie:
            headers['Cookie'] = 'owrt_remote_session=session-test'
        if agent:
            headers['Authorization'] = 'Bearer agent-test'
        raw = None
        if payload is not None:
            raw = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}{path}',
                                         data=raw, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.read(), response.headers
