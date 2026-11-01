"""Small, deliberately fallible loopback service. No third-party dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 18765
CONTROL = "/run/lsrp-lab/control.sock"
WORK_INPUT = b"lsrp-reliability-lab"
WORK_RESULT = hashlib.sha256(WORK_INPUT).hexdigest()
MAX_FAULT_SECONDS = 15.0


def process_identity() -> dict:
    # comm can contain spaces and ')': split after its final closing parenthesis.
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    return {
        "service": "lsrp-lab", "pid": os.getpid(),
        "start_ticks": int(fields[19]),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "instance": str(uuid.uuid4()),
    }


def cpu_worker(duration: float) -> None:
    """Self-expiring even if the control client or parent disappears."""
    deadline = time.monotonic() + duration
    block = b"lsrp bounded CPU workload" * 4096
    while time.monotonic() < deadline:
        hashlib.pbkdf2_hmac("sha256", block, b"lsrp", 2000)


class Modes:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.mode = "normal"
        self.deadline = 0.0
        self.worker: subprocess.Popen | None = None
        self.closed = threading.Event()
        self.watcher = threading.Thread(target=self._watch, name="fault-expiry", daemon=True)
        self.watcher.start()

    def _stop_worker(self) -> None:
        worker, self.worker = self.worker, None
        if worker is not None:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=0.5)
            else:
                worker.wait()

    def _normal(self) -> None:
        self._stop_worker()
        self.mode, self.deadline = "normal", 0.0

    def current(self) -> str:
        with self.lock:
            if self.mode != "normal" and time.monotonic() >= self.deadline:
                self._normal()
                print(json.dumps({"event": "fault_expired", "mode": "normal"}), flush=True)
            return self.mode

    def set(self, mode: str, duration: float) -> dict:
        if mode not in {"normal", "hang", "cpu"}:
            raise ValueError("mode must be normal, hang or cpu")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError("duration must be a finite number")
        if not math.isfinite(duration) or (mode != "normal" and not 0 < duration <= MAX_FAULT_SECONDS):
            raise ValueError("fault duration must be greater than 0 and at most 15 seconds")
        with self.lock:
            self._normal()
            if mode != "normal":
                deadline = time.monotonic() + duration
                if mode == "cpu":
                    self.worker = subprocess.Popen(
                        [sys.executable, "-u", str(Path(__file__).resolve()),
                         "--cpu-worker", str(duration)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, close_fds=True,
                    )
                self.mode, self.deadline = mode, deadline
            reply = {"ok": True, "mode": self.mode, "expires_monotonic": self.deadline,
                     "cpu_worker_pid": self.worker.pid if self.worker else None}
        print(json.dumps({"event": "mode_changed", **reply}), flush=True)
        return reply

    def _watch(self) -> None:
        while not self.closed.wait(0.05):
            self.current()

    def close(self) -> None:
        self.closed.set()
        with self.lock:
            self._normal()
        self.watcher.join(timeout=1)


class ServiceHTTP(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True

    def __init__(self, address: tuple, modes: Modes, identity: dict) -> None:
        self.modes, self.identity = modes, identity
        self.capacity = threading.BoundedSemaphore(12)
        super().__init__(address, Handler)

    def get_request(self):
        conn, addr = super().get_request()
        conn.settimeout(2.0)
        return conn, addr

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "LSRP/1"
    protocol_version = "HTTP/1.0"

    def log_message(self, format, *args):
        # Deliberately avoid arbitrary URLs/client data in the journal.
        pass

    def do_GET(self):
        if self.path not in {"/live", "/ready", "/work"}:
            self.send_error(404)
            return
        server = self.server
        while self.path != "/live" and server.modes.current() == "hang":
            if server.modes.closed.wait(0.025):
                break
        if self.path == "/work":
            hashlib.pbkdf2_hmac("sha256", WORK_INPUT, b"lsrp-lab", 20000)
        body = {**server.identity, "mode": server.modes.current()}
        if self.path == "/work":
            body["result"] = WORK_RESULT
        raw = json.dumps(body, sort_keys=True).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionError, TimeoutError):
            pass


def root_peer(conn: socket.socket) -> bool:
    credentials = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _, uid, _ = struct.unpack("3i", credentials)
    return uid == 0


class ControlHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(1.0)
        try:
            if not root_peer(self.request):
                raise PermissionError("control requires a root peer")
            line = self.rfile.readline(4097)
            if len(line) > 4096 or not line.endswith(b"\n"):
                raise ValueError("control requires one newline-terminated JSON object <=4096 bytes")
            request = json.loads(line)
            if not isinstance(request, dict) or set(request) - {"mode", "duration"}:
                raise ValueError("unknown control fields")
            reply = self.server.modes.set(request.get("mode"), request.get("duration", 0))
        except (ValueError, TypeError, PermissionError, TimeoutError, OSError) as exc:
            reply = {"ok": False, "error": str(exc)}
        try:
            self.wfile.write(json.dumps(reply).encode() + b"\n")
        except ConnectionError:
            pass


class ControlServer(getattr(socketserver, "UnixStreamServer", socketserver.BaseServer)):
    def __init__(self, path: str, modes: Modes):
        if sys.platform != "linux":
            raise RuntimeError("control sockets require Linux")
        self.modes = modes
        super().__init__(path, ControlHandler)
        os.chmod(path, 0o600)


def serve() -> int:
    if sys.platform != "linux":
        raise RuntimeError("labsvc requires Linux")
    if os.geteuid() == 0:
        raise RuntimeError("labsvc must run as a non-root service account")
    identity = process_identity()
    stopping = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopping.set())
    modes = Modes()
    http = control = None
    threads = []
    try:
        http = ServiceHTTP((HOST, PORT), modes, identity)
        # systemd's RuntimeDirectory is cleared on each stopped invocation.
        control = ControlServer(CONTROL, modes)
        for server, name in ((http, "requests"), (control, "control")):
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, name=name)
            thread.start()
            threads.append(thread)
        print(json.dumps({"event": "ready", **identity, "listen": f"{HOST}:{PORT}"}), flush=True)
        stopping.wait()
        print(json.dumps({"event": "shutdown_started", **identity}), flush=True)
        return 0
    except OSError as exc:
        print(json.dumps({"event": "startup_failed", "errno": exc.errno, "error": str(exc)}), flush=True)
        return 1
    finally:
        modes.close()
        for server in (control, http):
            if server is not None:
                # shutdown only if serve_forever was started, otherwise it deadlocks.
                if threads:
                    server.shutdown()
                server.server_close()
        for thread in threads:
            thread.join(timeout=2)
        if control is not None:
            Path(CONTROL).unlink(missing_ok=True)
        print(json.dumps({"event": "shutdown_complete"}), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-worker", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.cpu_worker is not None:
        if not math.isfinite(args.cpu_worker) or not 0 < args.cpu_worker <= MAX_FAULT_SECONDS:
            parser.error("invalid CPU worker TTL")
        cpu_worker(args.cpu_worker)
        return 0
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
