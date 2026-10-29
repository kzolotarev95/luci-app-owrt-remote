"""Regression tests for notification long-poll admission and stale cursors."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import contextlib


ROOT = Path(__file__).resolve().parents[1]


def load_hub():
    state = tempfile.TemporaryDirectory(prefix="notification-load-state-")
    os.environ["OWRT_REMOTE_STATE_DIR"] = state.name
    os.environ["OWRT_REMOTE_DB"] = str(Path(state.name) / "hub.db")
    spec = importlib.util.spec_from_file_location("notification_load_hub", ROOT / "vps" / "owrt-remote-hub.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._notification_test_state = state
    return module


class NotificationLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = load_hub()

    @classmethod
    def tearDownClass(cls):
        state = getattr(cls.hub, "_notification_test_state", None)
        if state:
            state.cleanup()
        os.environ.pop("OWRT_REMOTE_STATE_DIR", None)
        os.environ.pop("OWRT_REMOTE_DB", None)

    def test_stale_cursor_does_not_return_old_notifications(self):
        rows = [{"id": "one", "serial": 4, "ts": 1, "title": "old"}]
        with mock.patch.object(self.hub, "load_notifications", return_value=rows):
            self.assertEqual(self.hub.list_notifications(after_serial=99), [])

    def test_same_client_can_have_only_one_active_long_poll(self):
        key = self.hub.notification_wait_context("session", "client-1234")
        self.assertTrue(self.hub.claim_notification_wait(key))
        self.assertFalse(self.hub.claim_notification_wait(key))
        self.hub.release_notification_wait(key)
        self.assertTrue(self.hub.claim_notification_wait(key))
        self.hub.release_notification_wait(key)

    def test_metrics_are_bounded_and_released(self):
        key = self.hub.notification_wait_context("session", "client-5678")
        before = self.hub.notification_wait_metrics()["active"]
        self.assertTrue(self.hub.claim_notification_wait(key))
        self.assertEqual(self.hub.notification_wait_metrics()["active"], before + 1)
        self.hub.release_notification_wait(key)
        self.assertEqual(self.hub.notification_wait_metrics()["active"], before)

    def test_concurrent_claims_have_one_winner(self):
        key = self.hub.notification_wait_context("session", "client-concurrent")
        winners = []
        barrier = threading.Barrier(8)

        def claim():
            barrier.wait()
            if self.hub.claim_notification_wait(key):
                winners.append(True)

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(len(winners), 1)
        self.hub.release_notification_wait(key)

    def test_successful_poll_logs_are_quiet_but_errors_are_kept(self):
        class FakeHandler:
            path = "/api/notifications/wait?client_id=test"

            def address_string(self):
                return "127.0.0.1"

            def log_date_time_string(self):
                return "now"

        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            with mock.patch.object(self.hub.sys, "stderr", output):
                self.hub.Handler.log_message(FakeHandler(), '"%s" %s %s', "GET", "200", "12")
                self.hub.Handler.log_message(FakeHandler(), '"%s" %s %s', "GET", "500", "12")
        self.assertIn("500", output.getvalue())
        self.assertNotIn("200", output.getvalue())


if __name__ == "__main__":
    unittest.main()
