"""Experiment contracts: fault boundaries, persistence and honest outcomes."""
import contextlib
import copy
import json
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

from lsrp import lab, runner


def sample(stamp, ok=True):
    identity = {"pid": 100, "start_ticks": 1000, "boot_id": "boot", "instance": "trial", "mode": "normal"}
    env = lambda value: {"status": "ok", "source": "fixture", "data": value}
    return {"monotonic": stamp, "completed_monotonic": stamp + .01,
            "probe": {"ok": ok, "latency_ms": 5, "identity": identity if ok else None,
                      "completed_monotonic": stamp + .01, "error": None if ok else "connection refused"},
            "unit": env({"ActiveState": "active" if ok else "inactive", "NRestarts": "0"}),
            "process": env(identity if ok else {}), "cgroup": env({}),
            "socket": env({"listeners": []}), "journal": env({"entries": []})}


class Clock:
    def __init__(self):
        self.value = 10

    def monotonic(self):
        self.value += .025
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class CgroupPath:
    def __init__(self, name="", quota="25000 100000", memory="134217728"):
        self.name, self.quota, self.memory = name, quota, memory

    def __truediv__(self, name):
        return CgroupPath(str(name), self.quota, self.memory)

    def read_text(self):
        return self.quota if self.name == "cpu.max" else self.memory


class RunnerTests(unittest.TestCase):
    def experiment(self, directory, *, fail_probe=True, injection_error=None,
                   quota="25000 100000", memory="134217728"):
        clock = Clock()
        count = 0
        def snapshot():
            nonlocal count
            count += 1
            return sample(clock.monotonic(), ok=not (fail_probe and count == 5))
        calls = []
        def systemctl(*args, **kwargs):
            calls.append(args)
            if args[0] == "kill" and injection_error:
                raise injection_error
            return ""
        cleanup = {"ok": True, "errors": [], "verification": {
            lab.COLLISION: "inactive", lab.TIMER: "inactive", lab.RECOVER: "inactive"}}
        real_path = Path
        def path(value):
            return CgroupPath(quota=quota, memory=memory) if str(value) == "/sys/fs/cgroup" else real_path(value)
        stack = contextlib.ExitStack()
        patches = [
            patch.object(lab, "locked", side_effect=contextlib.nullcontext),
            patch.object(lab, "environment", return_value={"kernel": "fixture"}),
            patch.object(lab, "cleanup", side_effect=lambda: copy.deepcopy(cleanup)),
            patch.object(lab, "identity", return_value={"pid": 100, "start_ticks": 1000, "boot_id": "boot"}),
            patch.object(lab, "properties", return_value={"ControlGroup": "/system.slice/lsrp-lab.service"}),
            patch.object(lab, "systemctl", side_effect=systemctl),
            patch.object(runner, "wait_ready", return_value=[sample(1)["probe"]] * 3),
            patch.object(runner, "Path", side_effect=path),
            patch.object(runner.time, "monotonic", side_effect=clock.monotonic),
            patch.object(runner.time, "sleep", side_effect=clock.sleep),
            patch.object(runner.observe, "snapshot", side_effect=snapshot),
            patch.object(runner.signal, "getsignal", return_value=signal.SIG_DFL),
            patch.object(runner.signal, "signal"),
        ]
        with stack:
            mocks = [stack.enter_context(p) for p in patches]
            record = runner.run_trial("crash", 2, real_path(directory) / "record.json")
        stored = json.loads((real_path(directory) / "record.json").read_text())
        return record, stored, calls, mocks

    def test_real_record_reports_recovery_only_after_observed_failure_and_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            record, stored, calls, _ = self.experiment(directory)
        self.assertEqual(record["outcome"], "recovered")
        self.assertEqual(stored["outcome"], "recovered")
        self.assertTrue(stored["functional_failure_observed"])
        self.assertTrue(stored["recovery"]["sustained"])
        self.assertTrue(stored["cleanup"]["ok"])
        self.assertLess(calls.index(("restart", lab.TIMER)),
                        calls.index(("kill", "--kill-whom=main", "--signal=SIGKILL", lab.UNIT)))

    def test_unobserved_fault_does_not_become_a_successful_experiment(self):
        with tempfile.TemporaryDirectory() as directory:
            record, stored, _, _ = self.experiment(directory, fail_probe=False)
        self.assertEqual(record["outcome"], "inconclusive")
        self.assertFalse(stored["incident_observed"])

    def test_interruption_persists_failure_and_runs_cleanup_and_restores_handlers(self):
        with tempfile.TemporaryDirectory() as directory:
            record, stored, _, mocks = self.experiment(
                directory, injection_error=runner.Interrupted("Interrupted by SIGTERM"))
        self.assertEqual(stored["outcome"], "failed")
        self.assertIn("SIGTERM", stored["error"])
        self.assertTrue(stored["cleanup"]["ok"])
        self.assertEqual(mocks[2].call_count, 2)
        restored = mocks[-1].call_args_list[-2:]
        self.assertEqual({call.args for call in restored},
                         {(signal.SIGINT, signal.SIG_DFL), (signal.SIGTERM, signal.SIG_DFL)})

    def test_missing_resource_caps_prevent_any_fault_dispatch(self):
        for quota, memory in [("max 100000", "134217728"), ("50000 100000", "134217728"),
                              ("25000 100000", "max"), ("25000 100000", "268435456")]:
            with self.subTest(quota=quota, memory=memory), tempfile.TemporaryDirectory() as directory:
                record, stored, calls, _ = self.experiment(directory, quota=quota, memory=memory)
                self.assertEqual(record["outcome"], "failed")
                self.assertIn("boundaries", stored["error"])
                self.assertFalse(any(args[0] == "kill" for args in calls))
                self.assertEqual(stored["injection"], {})

    def test_precondition_failure_does_not_touch_service(self):
        @contextlib.contextmanager
        def refused():
            raise lab.LabError("ownership mismatch")
            yield
        with patch.object(lab, "locked", refused), patch.object(lab, "cleanup") as cleanup, \
                patch.object(lab, "systemctl") as systemctl:
            with self.assertRaisesRegex(lab.LabError, "ownership"):
                runner.run_trial("crash", 2, Path("unused.json"))
        cleanup.assert_not_called()
        systemctl.assert_not_called()

    def test_invalid_durations_are_rejected_before_acquiring_lab(self):
        with patch.object(lab, "locked") as locked:
            for duration in [float("nan"), float("inf"), -1, 0, 1.9, 12.1, 1000]:
                with self.subTest(duration=duration), self.assertRaises(lab.LabError):
                    runner.run_trial("hang", duration, Path("unused.json"))
        locked.assert_not_called()

    def test_atomic_output_replaces_symlink_without_overwriting_its_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, output = root / "unrelated.json", root / "trial.json"
            target.write_text("unrelated content")
            try:
                output.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("Creating symlinks unavailable")
            runner.write_json(output, {"trial_id": "new"})
            self.assertEqual(target.read_text(), "unrelated content")
            self.assertFalse(output.is_symlink())
            self.assertEqual(json.loads(output.read_text())["trial_id"], "new")


if __name__ == "__main__":
    unittest.main()
