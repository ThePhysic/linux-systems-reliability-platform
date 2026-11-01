import hashlib
import http.client
import io
import json
import math
import socket
import threading
import time
import unittest
from unittest.mock import patch

from lsrp import service


class ModesTests(unittest.TestCase):
    def setUp(self):
        self.modes = service.Modes()

    def tearDown(self):
        self.modes.close()

    def test_rejects_unbounded_and_nonfinite_faults(self):
        for duration in [-1, 0, 16, float("nan"), float("inf"), True, "4"]:
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                self.modes.set("hang", duration)
        self.assertEqual(self.modes.current(), "normal")

    def test_hang_automatically_expires_without_an_operator(self):
        self.modes.set("hang", 0.08)
        self.assertEqual(self.modes.current(), "hang")
        time.sleep(0.12)
        self.assertEqual(self.modes.current(), "normal")

    def test_cpu_child_expires_and_is_reaped(self):
        self.modes.set("cpu", 0.1)
        worker = self.modes.worker
        self.assertIsNotNone(worker)
        deadline = time.monotonic() + 3
        while self.modes.current() != "normal" and time.monotonic() < deadline:
            time.sleep(0.025)
        self.assertEqual(self.modes.current(), "normal")
        self.assertIsNotNone(worker.poll())
        self.assertIsNone(self.modes.worker)

    def test_reset_cancels_cpu_child(self):
        self.modes.set("cpu", 10)
        worker = self.modes.worker
        self.modes.set("normal", 0)
        self.assertIsNotNone(worker.poll())
        self.assertEqual(self.modes.current(), "normal")


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.modes = service.Modes()
        self.identity = {"service": "lsrp-lab", "pid": 123, "start_ticks": 5,
                         "boot_id": "boot", "instance": "instance"}
        self.server = service.ServiceHTTP(("127.0.0.1", 0), self.modes, self.identity)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02})
        self.thread.start()

    def tearDown(self):
        self.modes.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, timeout=1):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=timeout)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_work_has_stable_result_and_process_identity(self):
        status, raw = self.request("/work")
        data = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(data["result"], hashlib.sha256(b"lsrp-reliability-lab").hexdigest())
        for key, value in self.identity.items():
            self.assertEqual(data[key], value)
        self.assertEqual(data["mode"], "normal")

    def test_liveness_survives_hang_and_work_recovers_by_expiry(self):
        self.modes.set("hang", 0.3)
        self.assertEqual(self.request("/live")[0], 200)
        with self.assertRaises((TimeoutError, socket.timeout)):
            self.request("/ready", timeout=0.05)
        time.sleep(0.35)
        self.assertEqual(self.request("/work")[0], 200)

    def test_unknown_endpoint_is_not_healthy(self):
        self.assertEqual(self.request("/does-not-exist")[0], 404)


class ControlTests(unittest.TestCase):
    def request(self, content, privileged=True):
        handler = service.ControlHandler.__new__(service.ControlHandler)
        handler.request = unittest.mock.Mock()
        handler.rfile = io.BytesIO(content)
        handler.wfile = io.BytesIO()
        modes = service.Modes()
        handler.server = unittest.mock.Mock(modes=modes)
        try:
            with patch.object(service, "root_peer", return_value=privileged):
                handler.handle()
            return json.loads(handler.wfile.getvalue()), modes.current()
        finally:
            modes.close()

    def test_unprivileged_control_cannot_change_state(self):
        reply, mode = self.request(b'{"mode":"hang","duration":10}\n', privileged=False)
        self.assertFalse(reply["ok"])
        self.assertEqual(mode, "normal")

    def test_requires_bounded_well_formed_control(self):
        for content in [b'{}', b'x' * 4097 + b'\n', b'{"mode":"hang","duration":100}\n',
                        b'{"mode":"hang","duration":1,"unknown":true}\n', b'[]\n']:
            with self.subTest(content=content[:60]):
                reply, mode = self.request(content)
                self.assertFalse(reply["ok"])
                self.assertEqual(mode, "normal")

    def test_valid_root_control_changes_state(self):
        reply, mode = self.request(b'{"mode":"hang","duration":1}\n')
        self.assertTrue(reply["ok"])
        self.assertEqual(mode, "hang")


class StartupTests(unittest.TestCase):
    def test_second_server_thread_failure_closes_unstarted_control_without_shutdown_deadlock(self):
        http, control = unittest.mock.Mock(), unittest.mock.Mock()
        first, second = unittest.mock.Mock(), unittest.mock.Mock()
        second.start.side_effect = RuntimeError("can't start new thread")
        with patch.object(service.sys, "platform", "linux"), \
                patch.object(service.os, "geteuid", return_value=991, create=True), \
                patch.object(service, "process_identity", return_value={"pid": 123}), \
                patch.object(service.signal, "signal"), \
                patch.object(service, "Modes") as modes, \
                patch.object(service, "ServiceHTTP", return_value=http), \
                patch.object(service, "ControlServer", return_value=control), \
                patch.object(service.threading, "Thread", side_effect=[first, second]), \
                patch.object(service, "Path"), patch.object(service.sys, "stdout", io.StringIO()):
            result = service.serve()
        self.assertEqual(result, 1)
        # BaseServer.shutdown waits forever if serve_forever has never started.
        control.shutdown.assert_not_called()
        control.server_close.assert_called_once()
        http.shutdown.assert_called_once()
        http.server_close.assert_called_once()
        modes.return_value.close.assert_called_once()
        first.join.assert_called_once()


if __name__ == "__main__":
    unittest.main()
