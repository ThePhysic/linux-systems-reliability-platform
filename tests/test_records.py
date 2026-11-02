"""Saved-input contracts: errors have locations and valid evidence is unchanged."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from lsrp import cli, records, report, stats

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    envelope = lambda data: {"status": "ok", "data": data}
    identity = {"pid": 123, "start_ticks": 100, "boot_id": "boot", "instance": "instance", "mode": "normal"}
    return {"schema_version": 1, "trial_id": "fixture", "scenario": "hang", "outcome": "inconclusive",
            "baseline": [], "samples": [{"monotonic": 1, "probe": {
                "ok": True, "identity": identity, "latency_ms": 5},
                "process": envelope(copy.deepcopy(identity)), "unit": envelope({"NRestarts": "0"}),
                "cgroup": envelope({"path": "/lab", "generation": {"device": 1, "inode": 2},
                                      "files": {"cpu.stat": {"status": "ok", "value": {"nr_throttled": 0}}}}),
                "journal": envelope({"entries": [{"MESSAGE": "message", "__MONOTONIC_TIMESTAMP": "1000000"}]}),
                "socket": envelope({"listeners": []})}],
            "events": [{"monotonic": 1, "phase": "fixture", "message": "test"}],
            "metrics": {}, "injection": {"details": {}}, "limitations": [],
            "cleanup": {"ok": False, "errors": ["not attempted"], "verification": {}, "functional_checks": []}}


def replace(record, path, value):
    parent = record
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value


class SavedRecordTests(unittest.TestCase):
    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_rejected_everywhere(self, path, location):
        for command in [["report", str(path)], ["report", str(path), "--json"],
                        ["compare", str(path)], ["compare", str(path), "--json"]]:
            with self.subTest(command=command):
                code, out, error = self.invoke(command)
                self.assertEqual(code, 2, out + error)
                self.assertIn(location, error)
                self.assertIn(str(path), error)
                self.assertNotIn("Traceback", error)
                self.assertEqual(out, "")

    def test_null_timeline_entry_fails_cleanly_in_real_cli_processes(self):
        record = fixture()
        record["events"] = [None]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            for command in [["report", str(path)], ["report", str(path), "--json"],
                            ["compare", str(path)], ["compare", str(path), "--json"]]:
                result = subprocess.run([sys.executable, "-m", "lsrp", *command], cwd=ROOT,
                                        text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 2)
                self.assertIn("events[0]", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_malformed_nested_fields_are_rejected_before_rendering_or_comparing(self):
        cases = [
            (("events", 0), None, "events[0]"),
            (("events", 0, "message"), {}, "events[0].message"),
            (("events", 0, "monotonic"), float("nan"), "events[0].monotonic"),
            (("samples", 0), None, "samples[0]"),
            (("samples", 0, "probe"), None, "samples[0].probe"),
            (("samples", 0, "probe", "ok"), "yes", "samples[0].probe.ok"),
            (("samples", 0, "probe", "identity"), [], "samples[0].probe.identity"),
            (("samples", 0, "probe", "identity", "pid"), [], "samples[0].probe.identity.pid"),
            (("samples", 0, "probe", "identity", "boot_id"), {}, "samples[0].probe.identity.boot_id"),
            (("samples", 0, "process", "data"), [], "samples[0].process.data"),
            (("samples", 0, "process", "data", "pid"), True, "samples[0].process.data.pid"),
            (("samples", 0, "unit", "data", "NRestarts"), "unknown", "samples[0].unit.data.NRestarts"),
            (("samples", 0, "unit", "data", "InvocationID"), [], "samples[0].unit.data.InvocationID"),
            (("samples", 0, "cgroup", "data", "files"), [], "samples[0].cgroup.data.files"),
            (("samples", 0, "cgroup", "data", "files", "cpu.stat", "value"), [], '["cpu.stat"].value'),
            (("samples", 0, "cgroup", "data", "files", "cpu.stat", "value", "nr_throttled"), "4", "nr_throttled"),
            (("samples", 0, "cgroup", "data", "generation", "inode"), {}, "generation.inode"),
            (("samples", 0, "journal", "data", "entries", 0), None, "journal.data.entries[0]"),
            (("samples", 0, "journal", "data", "entries", 0, "__MONOTONIC_TIMESTAMP"), [], "__MONOTONIC_TIMESTAMP"),
            (("samples", 0, "journal", "data", "entries", 0, "__MONOTONIC_TIMESTAMP"), "unknown", "__MONOTONIC_TIMESTAMP"),
            (("samples", 0, "journal", "data", "entries", 0, "MESSAGE"), {}, "entries[0].MESSAGE"),
            (("samples", 0, "socket", "data", "listeners"), [{}], "socket.data.listeners[0]"),
            (("cleanup", "errors"), [{}], "cleanup.errors[0]"),
            (("cleanup", "verification"), [], "cleanup.verification"),
            (("cleanup", "functional_checks"), [None], "cleanup.functional_checks[0]"),
            (("limitations",), [{}], "limitations[0]"),
            (("metrics", "detection_latency_s"), "unknown", "metrics.detection_latency_s"),
            (("metrics", "failure_onset_bounds_monotonic"), [0, "unknown"], "failure_onset_bounds_monotonic[1]"),
            (("injection", "details"), [], "injection.details"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.json"
            for changed, value, location in cases:
                with self.subTest(changed=changed, value=value):
                    record = fixture()
                    replace(record, changed, value)
                    path.write_text(json.dumps(record), encoding="utf-8")
                    self.assert_rejected_everywhere(path, location)

    def test_nonfinite_values_and_numeric_overflow_are_rejected_even_in_extension_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nonfinite.json"
            for number in ["NaN", "Infinity", "-Infinity", "1e999", "9" * 400]:
                path.write_text(json.dumps(fixture())[:-1] + ', "extension": ' + number + '}', encoding="utf-8")
                with self.subTest(number=number[:30]):
                    self.assert_rejected_everywhere(path, "extension")

    def test_duplicate_json_fields_and_excessive_nesting_fail_without_tracebacks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            base = json.dumps(fixture())
            cases = [(base[:-1] + ', "events": []}', "duplicate JSON object key"),
                     (base[:-1] + ', "extension": ' + '[' * 70 + 'null' + ']' * 70 + '}', "nesting"),
                     (base[:-1] + ', "extension": ' + '[' * 2000 + 'null' + ']' * 2000 + '}', "nesting")]
            for raw, message in cases:
                with self.subTest(message=message, length=len(raw)):
                    path.write_text(raw, encoding="utf-8")
                    self.assert_rejected_everywhere(path, message)

    def test_missing_checkpoint_fields_and_null_os_observations_are_preserved(self):
        record = {"schema_version": 1, "trial_id": "checkpoint", "scenario": "collision", "samples": [{
            "monotonic": 1, "probe": {"ok": False, "identity": None, "latency_ms": None},
            "unit": {"status": "unavailable", "data": None, "error": "permission denied"},
            "process": {"status": "unavailable", "data": None},
            "cgroup": {"status": "ok", "data": {"files": {
                "cpu.stat": {"status": "unavailable", "value": None, "error": "not readable"}}}},
            "journal": {"status": "unavailable", "data": None}}]}
        original = copy.deepcopy(record)
        self.assertIs(records.validate_trial(record), record)
        self.assertEqual(record, original)
        self.assertIn("Insufficient evidence", report.render(record))
        self.assertIsNone(stats.summarize([record])["scenarios"]["collision"]["metrics"]["recovery_time_s"]["median"])

    def test_binary_journal_message_remains_valid_evidence(self):
        record = fixture()
        record["samples"][0]["journal"]["data"]["entries"][0]["MESSAGE"] = [0, 127, 255]
        self.assertIs(records.validate_trial(record), record)
        report.render(record)

    def test_all_checked_in_real_trials_and_interrupted_checkpoints_remain_compatible(self):
        paths = []
        for path in (ROOT / "examples").rglob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("trial_id"):
                paths.append(path)
                with self.subTest(path=path.relative_to(ROOT)):
                    loaded = cli.read_trial(path)
                    self.assertEqual(loaded, data)
                    self.assertIn(data["trial_id"], report.render(loaded))
                    self.assertEqual(stats.summarize([loaded])["total_trials"], 1)
                    for command in [["report", str(path), "--json"], ["compare", str(path), "--json"]]:
                        code, out, error = self.invoke(command)
                        self.assertEqual(code, 0, error)
                        decoded = json.loads(out)
                        if command[0] == "report":
                            self.assertEqual(decoded, data)
        self.assertGreaterEqual(len(paths), 16)
        self.assertTrue(any("before-sigkill" in path.name for path in paths))
        self.assertTrue(any("interrupted" in path.name for path in paths))


if __name__ == "__main__":
    unittest.main()
