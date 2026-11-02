"""Real socket regressions for shared request budgets and resource cleanup."""
import contextlib
import json
import socketserver
import threading
import time
import unittest
from unittest.mock import patch

from lsrp import observe


@contextlib.contextmanager
def responding(segments):
    stop = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(1)
            try:
                request = b""
                while b"\r\n\r\n" not in request and len(request) < 4096:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    request += chunk
                for delay, payload in segments:
                    if stop.wait(delay):
                        break
                    self.request.sendall(payload)
            except OSError:
                pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = False
        block_on_close = True

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()
        try:
            with patch.object(observe, "PORT", server.server_address[1]):
                yield
        finally:
            stop.set()
            server.shutdown()
            thread.join(timeout=2)
            if thread.is_alive():
                raise AssertionError("Owned loopback server did not stop")


class ProbeDeadlineTests(unittest.TestCase):
    def assert_deadline(self, segments, *, timeout=.2, status=None):
        with responding(segments):
            started = time.monotonic()
            result = observe.probe(timeout=timeout)
            elapsed = time.monotonic() - started
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "timeout", result)
        self.assertLess(elapsed, timeout + .3, result)
        self.assertGreaterEqual(result["latency_ms"], 0)
        if status is not None:
            self.assertEqual(result["http_status"], status)

    def test_slow_status_line_cannot_reset_deadline(self):
        self.assert_deadline([(.04, bytes([byte])) for byte in b"HTTP/1.1 200 OK\r\n"])

    def test_slow_headers_cannot_reset_deadline(self):
        self.assert_deadline([(0, b"HTTP/1.1 200 OK\r\nX-Slow: ")]
                             + [(.04, b"x") for _ in range(30)])

    def test_slow_body_hits_total_deadline_despite_continuous_progress(self):
        self.assert_deadline([(0, b"HTTP/1.1 200 OK\r\nContent-Length: 30\r\n\r\n")]
                             + [(.04, b" ") for _ in range(30)], status=200)

    def test_headers_and_body_share_one_budget(self):
        self.assert_deadline([(.12, b"HTTP/1.1 200 OK\r\nContent-Length: 30\r\n\r\n")]
                             + [(.04, b" ") for _ in range(30)], status=200)

    def test_chunked_body_cannot_reset_deadline(self):
        self.assert_deadline([(0, b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")]
                             + [(.04, b"1\r\nx\r\n") for _ in range(30)], status=200)

    def test_chunked_trailers_cannot_reset_deadline(self):
        self.assert_deadline([(0, b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nX-Slow: ")]
                             + [(.04, b"x") for _ in range(30)], status=200)

    def test_oversized_body_remains_invalid(self):
        with responding([(0, b"HTTP/1.1 200 OK\r\nContent-Length: 8193\r\n\r\n" + b"x" * 8193)]):
            result = observe.probe(timeout=1)
        self.assertEqual(result["error_kind"], "invalid_response")
        self.assertIn("Oversized", result["error"])

    def test_deeply_nested_response_is_a_failed_probe_without_traceback(self):
        body = b"[" * 1200 + b"]" * 1200
        with responding([(0, f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)]):
            result = observe.probe(timeout=1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "invalid_response")

    def test_complete_work_response_preserves_identity_validation(self):
        identity = {"service": "lsrp-lab", "pid": 123, "start_ticks": 456,
                    "boot_id": "00000000-0000-0000-0000-000000000001",
                    "instance": "00000000-0000-0000-0000-000000000002", "mode": "normal"}
        body = json.dumps({**identity, "result": observe.WORK_RESULT}).encode()
        for framing in [f"Content-Length: {len(body)}\r\n".encode(), b"Transfer-Encoding: chunked\r\n"]:
            payload = (f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
                       if framing.startswith(b"Transfer") else body)
            with self.subTest(framing=framing), responding([(0, b"HTTP/1.1 200 OK\r\n" + framing + b"\r\n" + payload)]), \
                    patch.object(observe, "_verify_local_identity", return_value={"status": "ok", "data": identity}):
                result = observe.probe(timeout=1)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["identity"], identity)

    def test_invalid_timeout_never_starts_network_io(self):
        with patch.object(observe, "get_work") as request:
            for value in [0, -1, float("nan"), float("inf"), True, "0.4"]:
                with self.subTest(value=value):
                    self.assertEqual(observe.probe(timeout=value)["error_kind"], "invalid_response")
        request.assert_not_called()
