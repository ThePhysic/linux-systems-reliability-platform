import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

from lsrp import observe


class ObservationTests(unittest.TestCase):
    def test_proc_stat_handles_spaces_and_parentheses(self):
        fields = ["S", "1"] + ["0"] * 20
        fields[11], fields[12], fields[19], fields[21] = "100", "50", "123456", "7"
        data = observe.parse_stat("42 (worker (odd) name) " + " ".join(fields), 100, 4096)
        self.assertEqual(data["pid"], 42)
        self.assertEqual(data["comm"], "worker (odd) name")
        self.assertEqual(data["cpu_seconds"], 1.5)
        self.assertEqual(data["rss_bytes"], 28672)
        self.assertEqual(data["start_ticks"], 123456)

    def test_missing_systemd_is_unavailable_not_zero(self):
        with patch.object(observe, "command", side_effect=FileNotFoundError("systemctl absent")):
            value = observe.unit_state()
        self.assertEqual(value["status"], "unavailable")
        self.assertIsNone(value["data"])
        self.assertIn("absent", value["error"])
        self.assertTrue(value["collected_at"])

    def test_commands_ignore_caller_path_and_dynamic_loader_environment(self):
        with patch.dict(observe.os.environ, {"PATH": "/malicious", "LD_PRELOAD": "/malicious.so",
                                            "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/unrelated"}), \
                patch.object(observe.subprocess, "run") as run:
            observe.command([observe.SYSTEMCTL, "--version"])
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/systemctl")
        self.assertNotIn("LD_PRELOAD", run.call_args.kwargs["env"])
        self.assertNotIn("DBUS_SYSTEM_BUS_ADDRESS", run.call_args.kwargs["env"])
        self.assertNotEqual(run.call_args.kwargs["env"]["PATH"], "/malicious")
        with self.assertRaises(ValueError):
            observe.command(["systemctl", "--version"])

    def test_cgroup_partial_observations_preserve_missingness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit = root / "system.slice" / "lsrp-lab.service"
            unit.mkdir(parents=True)
            (unit / "cpu.stat").write_text("usage_usec 123\nnr_throttled 4\n")
            with patch.object(observe, "CGROUP", root):
                value = observe.cgroup_state("/system.slice/lsrp-lab.service")
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["data"]["files"]["cpu.stat"]["value"]["nr_throttled"], 4)
        self.assertIsNone(value["data"]["files"]["memory.current"]["value"])
        self.assertEqual(value["data"]["files"]["memory.current"]["status"], "unavailable")

    def test_cgroup_path_cannot_escape_scope_or_read_host_root(self):
        self.assertEqual(observe.cgroup_state("/")["status"], "unavailable")
        self.assertEqual(observe.cgroup_state("/../../etc")["status"], "unavailable")

    def test_cgroup_malformed_values_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit = root / "lab"
            unit.mkdir()
            (unit / "cpu.stat").write_text("nr_throttled nope\n")
            with patch.object(observe, "CGROUP", root):
                value = observe.cgroup_state("/lab")
        self.assertEqual(value["data"]["files"]["cpu.stat"]["status"], "unavailable")

    def test_timeout_and_refusal_are_distinct(self):
        for reason, kind in ((TimeoutError("slow"), "timeout"), (ConnectionRefusedError("closed"), "connection_refused")):
            with self.subTest(kind=kind), patch.object(observe, "build_opener") as opener:
                opener.return_value.open.side_effect = URLError(reason)
                value = observe.probe()
                self.assertFalse(value["ok"])
                self.assertEqual(value["error_kind"], kind)
                self.assertGreaterEqual(value["latency_ms"], 0)

    def test_an_unrelated_healthy_listener_is_not_recovery(self):
        payload = {"status": "healthy", "service": "somebody-else"}
        with patch.object(observe, "build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = json.dumps(payload).encode()
            value = observe.probe()
        self.assertFalse(value["ok"])
        self.assertEqual(value["error_kind"], "invalid_response")

    def test_identity_validation_rejects_wrong_work_and_pid_types(self):
        data = {"service": "lsrp-lab", "pid": True, "start_ticks": 3,
                "boot_id": "00000000-0000-0000-0000-000000000001",
                "instance": "00000000-0000-0000-0000-000000000002", "mode": "normal", "result": observe.WORK_RESULT}
        with self.assertRaises(ValueError):
            observe._validate_identity(data)
        data["pid"] = 123
        data["result"] = "incorrect"
        with self.assertRaises(ValueError):
            observe._validate_identity(data)

    def test_response_identity_must_match_local_proc(self):
        identity = {"pid": 123, "start_ticks": 456, "boot_id": "actual-boot"}
        with patch.object(Path, "read_text", side_effect=["unused", "actual-boot"]), \
                patch.object(observe, "parse_stat", return_value={"pid": 123, "start_ticks": 999}), \
                patch.object(observe.os, "sysconf", return_value=100, create=True):
            result = observe._verify_local_identity(identity)
        self.assertEqual(result["status"], "error")
        self.assertIn("disagrees", result["error"])

    def test_work_response_without_verifiable_local_identity_is_not_success(self):
        payload = {"service": "lsrp-lab", "pid": 123, "start_ticks": 456,
                   "boot_id": "00000000-0000-0000-0000-000000000001",
                   "instance": "00000000-0000-0000-0000-000000000002",
                   "mode": "normal", "result": observe.WORK_RESULT}
        with patch.object(observe, "build_opener") as opener, \
                patch.object(observe, "_verify_local_identity", return_value={"status": "unavailable", "error": "PID disappeared"}):
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = json.dumps(payload).encode()
            result = observe.probe()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_kind"], "identity_unverified")

    def test_redirect_is_rejected_without_contacting_second_loopback_server(self):
        requests = {"target": 0}

        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                requests["target"] += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"This target must never receive a probe")

            def log_message(self, *_args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Target) as target:
            class Redirect(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{target.server_port}/not-the-lab")
                    self.end_headers()

                def log_message(self, *_args):
                    pass

            with ThreadingHTTPServer(("127.0.0.1", 0), Redirect) as redirect:
                threads = [threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
                           for server in (target, redirect)]
                for thread in threads:
                    thread.start()
                try:
                    with patch.object(observe, "PORT", redirect.server_port):
                        result = observe.probe(timeout=1)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["http_status"], 302)
                    self.assertEqual(result["error_kind"], "http_error")
                    self.assertIn("redirect rejected", result["error"])
                    self.assertEqual(requests["target"], 0)
                finally:
                    redirect.shutdown()
                    target.shutdown()
                    for thread in threads:
                        thread.join(timeout=2)

    def test_journal_is_unit_scoped_and_drops_unrelated_fields(self):
        entry = {"MESSAGE": "service started", "_SYSTEMD_UNIT": observe.UNIT, "UNRELATED_SECRET": "do not collect"}
        result = subprocess.CompletedProcess([], 0, json.dumps(entry) + "\n", "")
        with patch.object(observe, "command", return_value=result) as cmd:
            value = observe.journal_state()
        self.assertIn("--unit=lsrp-lab.service", cmd.call_args.args[0])
        self.assertNotIn("UNRELATED_SECRET", value["data"]["entries"][0])

    def test_snapshot_rejects_response_from_another_main_process(self):
        env = lambda data: {"status": "ok", "data": data}
        response = {"ok": True, "identity": {"pid": 999, "start_ticks": 8, "boot_id": "boot"}}
        with patch.object(observe, "unit_state", return_value=env({"MainPID": "123", "ActiveState": "active"})), \
                patch.object(observe, "process_state", return_value=env({"pid": 123, "start_ticks": 7, "boot_id": "boot"})), \
                patch.object(observe, "cgroup_state", return_value=env({})), \
                patch.object(observe, "socket_state", return_value=env({})), \
                patch.object(observe, "journal_state", return_value=env({})), \
                patch.object(observe, "probe", return_value=response):
            value = observe.snapshot()
        self.assertFalse(value["probe"]["ok"])
        self.assertEqual(value["probe"]["error_kind"], "identity_mismatch")

    def test_snapshot_requires_affirmative_supervised_process_identity(self):
        healthy = {"ok": True, "identity": {"pid": 123, "start_ticks": 7, "boot_id": "boot", "mode": "normal"}}
        env = lambda data: {"status": "ok", "data": data}
        unavailable = {"status": "unavailable", "data": None, "error": "restricted"}
        cases = [
            (env({"MainPID": "0", "ActiveState": "inactive"}), unavailable, False),
            (unavailable, unavailable, False),
            (env({"MainPID": "123", "ActiveState": "active"}), unavailable, False),
            (env({"MainPID": "123", "ActiveState": "inactive"}), env(healthy["identity"]), False),
            (env({"MainPID": "123", "ActiveState": "active"}), env(healthy["identity"]), True),
        ]
        for unit, process, accepted in cases:
            with self.subTest(unit=unit, process=process), \
                    patch.object(observe, "unit_state", return_value=unit), \
                    patch.object(observe, "process_state", return_value=process), \
                    patch.object(observe, "cgroup_state", return_value=env({})), \
                    patch.object(observe, "socket_state", return_value=env({})), \
                    patch.object(observe, "journal_state", return_value=env({})), \
                    patch.object(observe, "probe", return_value=dict(healthy)):
                value = observe.snapshot()
            self.assertEqual(value["probe"]["ok"], accepted)
            if not accepted:
                self.assertEqual(value["probe"]["error_kind"], "identity_unverified")


if __name__ == "__main__":
    unittest.main()
