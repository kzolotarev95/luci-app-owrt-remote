"""Resource regression tests; only temporary state and loopback servers are used.

Run: python -m unittest discover -s tests -v
Soak: python tests/test_resource_lifecycle.py --soak-seconds 600
Linux measures /proc/self/fd (including hub.db); Windows measures process handles.
Connections are deliberately retained so GC cannot conceal missing close().
"""
import argparse
import ast
import contextlib
import ctypes
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
import urllib.error
import urllib.request


SOURCE = Path(os.environ.get("OWRT_RESOURCE_HUB_SOURCE", Path(__file__).resolve().parents[1] / "vps" / "owrt-remote-hub.py"))
STATE = tempfile.TemporaryDirectory(prefix="owrt-resource-tests-")
spec = importlib.util.spec_from_file_location("resource_test_hub", SOURCE)
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
with mock.patch.dict(os.environ, {"OWRT_REMOTE_STATE_DIR": STATE.name, "OWRT_REMOTE_ROUTER_NOTIFY_POLL": "3"}):
    spec.loader.exec_module(hub)
REAL_CONNECT = sqlite3.connect


class TrackedConnection(sqlite3.Connection):
    def close(self):
        super().close()
        self.closed = True


def track_connections(retained):
    def factory(*args, **kwargs):
        conn = REAL_CONNECT(*args, factory=TrackedConnection, **kwargs)
        conn.closed = False
        retained.append(conn)
        return conn
    return mock.patch.object(hub.sqlite3, "connect", side_effect=factory)


def resource_counts():
    if sys.platform.startswith("linux"):
        links = []
        for path in Path("/proc/self/fd").iterdir():
            try:
                links.append(os.readlink(path))
            except FileNotFoundError:
                pass
        return {"fd": len(links), "hub_db_fd": sum("hub.db" in item for item in links)}
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        count = ctypes.c_ulong()
        if not kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"handles": count.value}
    return {}


class QuietHandler(hub.Handler):
    def log_message(self, *args):
        pass


class RouterBackend(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"router proxy fixture"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class HubFixture:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="owrt-db-", dir=STATE.name)
        self.app = hub.App(Path(self.tmp.name) / "hub.db", "test-session", "test-agent", "")
        hub.write_json_private(hub.AUTH_FILE, {"username": "test-owner", "password_hash": "unused"})
        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), RouterBackend)
        with self.app.conn() as conn:
            hub.upsert_router(conn, {
                "id": "test-router", "name": "Test Router", "entry_port": self.backend.server_port,
                "vps_host": "127.0.0.1", "public_url": "http://127.0.0.1",
            })
        self.server = hub.HubHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.server.app = self.app
        self.server.is_tls = False
        self.threads = []
        self.push_patch = mock.patch.object(hub, "queue_web_push_payload")
        self.push_patch.start()
        for server in (self.backend, self.server):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.app.start_router_state_monitor()
        return self

    def request(self, path, payload=None):
        headers = {"Cookie": "owrt_remote_session=test-session", "Authorization": "Bearer test-agent"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}{path}", data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.read()

    def exercise(self):
        assert json.loads(self.request("/health"))["ok"]
        assert json.loads(self.request("/api/heartbeat", {"id": "test-router", "hostname": "test-router"}))["ok"]
        assert len(json.loads(self.request("/api/routers"))["routers"]) == 1
        assert "groups" in json.loads(self.request("/api/router-groups"))
        assert "notifications" in json.loads(self.request("/api/notifications"))
        assert self.request("/access/test-router/") == b"router proxy fixture"
        assert b"v112" in self.request("/")
        self.app.snapshot_router_states()

    def __exit__(self, *args):
        self.app.stop_router_state_monitor()
        for server in (self.server, self.backend):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)
        self.push_patch.stop()
        self.tmp.cleanup()


class SQLiteLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=STATE.name)
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "hub.db"
        self.app = hub.App(self.db, "test-session", "test-agent", "")
        self.retained = []
        self.patch = track_connections(self.retained)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def assert_closed(self):
        self.assertTrue(self.retained)
        for conn in self.retained:
            self.assertTrue(conn.closed)
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("select 1")

    def test_success_return_and_commit(self):
        def operation():
            with self.app.conn() as conn:
                conn.execute("create table sample(value integer)")
                conn.execute("insert into sample values (7)")
                return 7
        self.assertEqual(operation(), 7)
        with self.app.conn() as conn:
            self.assertEqual(conn.execute("select value from sample").fetchone()[0], 7)
        self.assert_closed()

    def test_exception_and_base_exception_rollback(self):
        with self.app.conn() as conn:
            conn.execute("create table sample(value integer)")
        for error in (ValueError("body failed"), KeyboardInterrupt()):
            with self.assertRaises(type(error)):
                with self.app.conn() as conn:
                    conn.execute("insert into sample values (9)")
                    raise error
        with self.app.conn() as conn:
            self.assertEqual(conn.execute("select count(*) from sample").fetchone()[0], 0)
        self.assert_closed()

    def test_init_failure_closes_and_propagates(self):
        with mock.patch.object(hub, "init_db", side_effect=sqlite3.OperationalError("init failed")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "init failed"):
                with self.app.conn():
                    self.fail("must not enter body")
        self.assert_closed()

    def test_commit_failure_rolls_back_and_closes(self):
        with hub.connect(self.db) as conn:
            conn.executescript("pragma foreign_keys=on; create table parent(id primary key); "
                               "create table child(id references parent(id) deferrable initially deferred);")
        with self.assertRaises(sqlite3.IntegrityError):
            with hub.connect(self.db) as conn:
                conn.execute("pragma foreign_keys=on")
                conn.execute("insert into child values (1)")
        with hub.connect(self.db) as conn:
            self.assertEqual(conn.execute("select count(*) from child").fetchone()[0], 0)
        self.assert_closed()

    def test_setup_and_transaction_exit_failure_close(self):
        for setup_error in (True, False):
            connection = mock.MagicMock()
            if setup_error:
                type(connection).row_factory = mock.PropertyMock(side_effect=RuntimeError("setup failed"))
            else:
                connection.__exit__.side_effect = sqlite3.OperationalError("rollback failed")
            with mock.patch.object(hub.sqlite3, "connect", return_value=connection):
                with self.assertRaises((RuntimeError, sqlite3.OperationalError)):
                    with hub.connect(self.db):
                        pass
            connection.close.assert_called_once()

    def test_backup_destination_open_failure_closes_source(self):
        with self.app.conn():
            pass
        with self.assertRaises(sqlite3.OperationalError):
            hub.copy_sqlite_backup(self.db, self.tmp.name)  # destination is a directory
        self.assert_closed()

    def test_backup_success(self):
        with self.app.conn() as conn:
            hub.upsert_router(conn, {"id": "backup-router", "name": "backup", "entry_port": 0, "vps_host": "localhost"})
        copy = Path(self.tmp.name) / "copy.db"
        hub.copy_sqlite_backup(self.db, copy)
        with hub.connect(copy) as conn:
            self.assertEqual(hub.list_router_rows(conn)[0]["id"], "backup-router")
        self.assert_closed()

    def test_snapshot_fd_regression_without_gc(self):
        with self.app.conn():
            pass
        before = resource_counts()
        for _ in range(500):
            self.assertEqual(self.app.snapshot_router_states(), {})
        after = resource_counts()
        self.assert_closed()
        self.assertGreaterEqual(len(self.retained), 501)
        for key, value in before.items():
            self.assertLessEqual(after[key], value + (2 if key != "hub_db_fd" else 0), (before, after))

    def test_all_sqlite_creation_uses_managed_factory(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        direct = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "sqlite3" and node.func.attr == "connect":
                direct.append(node)
            elif isinstance(node.func, ast.Name) and node.func.id == "connect" or isinstance(node.func, ast.Attribute) and node.func.attr == "conn":
                self.assertIsInstance(parents[node], ast.withitem, f"unmanaged DB operation at line {node.lineno}")
        self.assertEqual(len(direct), 1)


class OtherResourceTests(unittest.TestCase):
    def handler(self, data=b"", headers=None):
        handler = object.__new__(hub.Handler)
        handler.rfile = io.BytesIO(data)
        handler.headers = headers or {}
        return handler

    def test_chunked_valid_and_truncated_bodies(self):
        headers = {"Transfer-Encoding": "chunked"}
        self.assertEqual(self.handler(b"3\r\nabc\r\n0\r\n\r\n", headers).read_body(), b"abc")
        for data in (b"", b"3\r\nab", b"3\r\nabc", b"0\r\n", b"-1\r\n", b"\r\n"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.handler(data, headers).read_body()
        with self.assertRaises(ValueError):
            self.handler(b"short", {"Content-Length": "100"}).read_body()

    def test_oauth_http_error_closes_response(self):
        body = io.BytesIO(b'{"error":"denied"}')
        error = urllib.error.HTTPError("http://test", 403, "denied", {}, body)
        with mock.patch.object(hub.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(ValueError, "denied"):
                hub.oauth_fetch_json("http://test")
        self.assertTrue(body.closed)

    def test_push_success_and_failure_close_response(self):
        response = mock.Mock(status_code=410, text="gone")
        error = RuntimeError("push failed")
        error.response = response
        with mock.patch.object(hub, "web_push_ready", return_value=True), mock.patch.object(hub, "webpush", return_value=response) as push:
            self.assertEqual(hub.send_web_push({"endpoint": "https://test"}, {}), "ok")
            response.close.assert_called_once()
            response.reset_mock()
            push.side_effect = error
            self.assertEqual(hub.send_web_push({"endpoint": "https://test"}, {}), "gone")
            response.close.assert_called_once()

    def test_atomic_failure_removes_temp_and_preserves_target(self):
        with tempfile.TemporaryDirectory(dir=STATE.name) as name:
            target = Path(name) / "original.txt"
            target.write_text("original")
            with mock.patch.object(hub.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    hub.atomic_write_text(target, "new")
            self.assertEqual(target.read_text(), "original")
            self.assertEqual(list(Path(name).iterdir()), [target])

    def test_askpass_setup_failure_removes_directory(self):
        with tempfile.TemporaryDirectory(dir=STATE.name) as name:
            directory = Path(name) / "askpass"
            directory.mkdir()
            with mock.patch.object(hub.tempfile, "mkdtemp", return_value=str(directory)), mock.patch.object(hub.os, "chmod", side_effect=OSError("chmod failed")):
                with self.assertRaises(OSError):
                    self.handler().prepare_ssh_askpass({}, "fake-password")
            self.assertFalse(directory.exists())

    def test_https_failure_releases_listening_port(self):
        server = hub.make_http_server(None, "127.0.0.1", 0)
        port = server.server_port
        server.server_close()
        with self.assertRaises(OSError):
            hub.make_http_server(None, "127.0.0.1", port, "missing-cert.pem", "missing-key.pem")
        replacement = hub.make_http_server(None, "127.0.0.1", port)
        replacement.server_close()

    def test_terminal_reader_start_failure_cleans_process_and_map(self):
        session = {"id": "failed-thread", "pid": 123, "fd": 456}
        with mock.patch.object(hub.threading.Thread, "start", side_effect=RuntimeError("thread failed")), mock.patch.object(hub, "close_terminal_process") as close:
            with self.assertRaises(RuntimeError):
                self.handler().launch_ssh_http_reader(session)
            close.assert_called_once_with(123, 456)
        self.assertNotIn("failed-thread", hub.SSH_HTTP_SESSIONS)

    def test_initial_websocket_failure_cleans_terminal(self):
        handler = self.handler()
        handler.connection = object()
        with mock.patch.object(handler, "spawn_terminal_pty", return_value=(123, 456)), mock.patch.object(hub, "ws_send_frame", side_effect=BrokenPipeError()), mock.patch.object(hub, "close_terminal_process") as close:
            handler.run_terminal_session({}, ["sh"], "unavailable", "open", "sh", "terminal error")
            close.assert_called_once_with(123, 456)

    def test_terminal_input_failure_closes_owned_descriptor(self):
        handler = self.handler()
        handler.send_json = mock.Mock()
        session = {"alive": True, "fd": 456, "last_seen": 0, "lock": threading.Lock()}
        with mock.patch.dict(hub.SSH_HTTP_SESSIONS, {"input-test": session}, clear=True), mock.patch.object(hub.os, "dup", return_value=789), mock.patch.object(hub.os, "close") as close, mock.patch.object(hub, "write_pty_all", side_effect=TimeoutError("write failed")):
            handler.ssh_http_write("input-test", {"data": "hello"})
            close.assert_called_once_with(789)
            self.assertEqual(handler.send_json.call_args.args[0], 500)

    @unittest.skipUnless(hasattr(hub.signal, "SIGHUP"), "POSIX terminal signals")
    def test_terminal_shutdown_escalates_and_reaps(self):
        # One unsuccessful poll per signal, followed by the final blocking reap.
        with mock.patch.object(hub.os, "close") as close, mock.patch.object(hub.os, "waitpid", create=True, side_effect=[(0, 0)] * 3 + [(123, 0)]) as wait, mock.patch.object(hub.os, "kill") as kill, mock.patch.object(hub.time, "monotonic", side_effect=[0, 2, 0, 2, 0, 2]):
            hub.close_terminal_process(123, 456)
        close.assert_called_once_with(456)
        self.assertEqual([c.args[1] for c in kill.call_args_list], [hub.signal.SIGHUP, hub.signal.SIGTERM, hub.signal.SIGKILL])
        self.assertEqual(wait.call_args.args, (123, 0))

    def test_terminal_session_expiry_and_retained_close_notice(self):
        now = hub.now_ts()
        sessions = {
            "expired": {"alive": True, "last_seen": now - 901, "lock": threading.Lock()},
            "closed": {"alive": False, "last_seen": now, "closed_at": now - 61, "lock": threading.Lock()},
            "recent": {"alive": False, "last_seen": now, "closed_at": now, "lock": threading.Lock()},
            "active": {"alive": True, "last_seen": now, "lock": threading.Lock()},
        }
        with mock.patch.dict(hub.SSH_HTTP_SESSIONS, sessions, clear=True):
            hub.expire_ssh_http_sessions()
            self.assertEqual(set(hub.SSH_HTTP_SESSIONS), {"recent", "active"})

    def test_monitor_recovers_from_initial_database_error(self):
        app = hub.App("unused", "session", "agent", "")
        with mock.patch.object(app, "prime_router_state_snapshot", side_effect=sqlite3.OperationalError("initial DB error")), mock.patch.object(app, "check_router_state_changes") as check, mock.patch.object(app.router_monitor_stop, "wait", side_effect=[False, True]), contextlib.redirect_stderr(io.StringIO()) as errors:
            app.router_state_monitor_loop()
            check.assert_called_once()
        self.assertIn("initial DB error", errors.getvalue())

    def test_monitor_start_failure_does_not_break_cleanup(self):
        app = hub.App("unused", "session", "agent", "")
        with mock.patch.object(hub.threading.Thread, "start", side_effect=RuntimeError("start failed")):
            with self.assertRaises(RuntimeError):
                app.start_router_state_monitor()
        app.stop_router_state_monitor()

    def test_serve_start_failure_closes_main_server(self):
        app = hub.App(Path(STATE.name) / "serve-failure.db", "session", "agent", "")
        server = mock.Mock()
        args = argparse.Namespace(db=app.db_path, public_url="", host="127.0.0.1", port=0)
        with mock.patch.object(hub, "App", return_value=app), mock.patch.object(hub, "session_token", return_value="session"), mock.patch.object(hub, "agent_token", return_value="agent"), mock.patch.object(hub, "record_hub_start_event"), mock.patch.object(hub, "load_auth", return_value={}), mock.patch.object(hub, "make_http_server", return_value=server), mock.patch.object(app, "start_router_state_monitor", side_effect=RuntimeError("monitor failed")):
            with self.assertRaises(RuntimeError):
                hub.cmd_serve(args)
        server.server_close.assert_called_once()

    def test_serve_stop_closes_main_and_extra_servers(self):
        app = hub.App(Path(STATE.name) / "serve-stop.db", "session", "agent", "")
        main = mock.Mock()
        extra = mock.Mock()
        main.serve_forever.side_effect = KeyboardInterrupt()
        args = argparse.Namespace(db=app.db_path, public_url="", host="127.0.0.1", port=8088,
                                  extra_ports="18088", tls_ports="", tls_cert="", tls_key="")
        with mock.patch.object(hub, "App", return_value=app), mock.patch.object(hub, "session_token", return_value="session"), mock.patch.object(hub, "agent_token", return_value="agent"), mock.patch.object(hub, "record_hub_start_event"), mock.patch.object(hub, "load_auth", return_value={}), mock.patch.object(hub, "make_http_server", side_effect=[main, extra]), contextlib.redirect_stdout(io.StringIO()):
            hub.cmd_serve(args)
        main.server_close.assert_called_once()
        extra.shutdown.assert_called_once()
        extra.server_close.assert_called_once()
        self.assertFalse(app.router_monitor_thread.is_alive())

    def test_incoming_socket_has_finite_io_timeout(self):
        server = hub.make_http_server(None, "127.0.0.1", 0)
        self.addCleanup(server.server_close)
        with socket.create_connection(server.server_address) as client:
            incoming, _ = server.get_request()
            try:
                self.assertEqual(incoming.gettimeout(), 90)
            finally:
                incoming.close()

    @unittest.skipUnless(sys.platform.startswith("linux"), "real PTY/child reaping needs Linux")
    def test_real_terminal_child_is_reaped_and_fd_closed(self):
        handler = self.handler()
        pid, fd = handler.spawn_terminal_pty(dict(os.environ), ["/bin/sh", "-c", "sleep 60"], "pty", "open", "sh")
        hub.close_terminal_process(pid, fd)
        with self.assertRaises(OSError):
            os.fstat(fd)
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)


class IntegrationTests(unittest.TestCase):
    def test_panel_api_proxy_heartbeat_and_monitor(self):
        with HubFixture() as fixture:
            for _ in range(5):
                fixture.exercise()
            self.assertTrue(fixture.app.router_monitor_thread.is_alive())
            # Verify the monitor still emits the online -> offline transition.
            fixture.app.prime_router_state_snapshot()
            with fixture.app.conn() as conn:
                conn.execute("update routers set last_seen = ?", (hub.now_ts() - 3600,))
            with mock.patch.object(hub, "notify_router_offline") as notify:
                fixture.app.check_router_state_changes()
                notify.assert_called_once()
        self.assertFalse(fixture.app.router_monitor_thread.is_alive())


def soak(seconds):
    started = time.monotonic()
    cpu_start = time.process_time()
    samples = []
    operations = 0
    with HubFixture() as fixture:
        fixture.exercise()  # warm up auth, sessions, notifications and HTTP workers
        while True:
            fixture.exercise()
            operations += 1
            # Measure between requests, after worker sockets have closed.
            time.sleep(0.05)
            counts = resource_counts()
            sample = {"elapsed": round(time.monotonic() - started, 1), **counts}
            samples.append(sample)
            print(json.dumps(sample), flush=True)
            if time.monotonic() - started >= seconds:
                break
            time.sleep(min(5, max(0, seconds - (time.monotonic() - started))))
        assert fixture.app.router_monitor_thread.is_alive(), "router monitor stopped"
    for key in samples[0]:
        if key == "elapsed":
            continue
        assert samples[-1][key] <= samples[0][key] + (4 if key != "hub_db_fd" else 0), samples
    result = {"seconds": round(time.monotonic() - started, 1), "cpu_seconds": round(time.process_time() - cpu_start, 2),
              "cycles": operations, "first": samples[0], "last": samples[-1], "peak": {key: max(s[key] for s in samples) for key in counts}}
    print("SOAK_OK " + json.dumps(result), flush=True)


if __name__ == "__main__":
    if "--soak-seconds" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--soak-seconds", type=int, required=True)
        soak(parser.parse_args().soak_seconds)
    else:
        unittest.main()
