#!/usr/bin/env python3
"""Exercise real SIGTERM/SIGKILL recovery in the installed disposable LSRP lab.

Run as root, after installation, with a fresh output directory:
  /usr/bin/python3 -I scripts/validate-interruption.py /root/lsrp-interruption-run

This script deliberately signals only runner processes it created. It never
signals a service PID, accepts another unit, or changes the recovery deadline.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

# -I excludes the source checkout and Python environment customization. Tests
# exercise precisely the installation validated by lab.guard's manifest.
sys.path.insert(0, "/opt/lsrp")
from lsrp import lab, observe, runner  # noqa: E402

CLI = ["/usr/bin/python3", "-I", "-c",
       "import sys; sys.path.insert(0, '/opt/lsrp'); from lsrp.cli import main; raise SystemExit(main())"]
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}


class ValidationFailure(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def identity(request):
    data = request.get("identity") or {}
    values = tuple(data.get(k) for k in ("boot_id", "pid", "start_ticks", "instance"))
    return values if all(value is not None for value in values) else None


def read_record(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationFailure(f"Trial record is not an object: {path}")
    return data


def properties(unit, keys, timeout=2):
    text = lab.command(["/usr/bin/systemctl", "show", unit, "--property=" + ",".join(keys)], timeout=timeout)
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def integer_property(data, name):
    try:
        return int(data.get(name, "0"))
    except (TypeError, ValueError):
        return None


class Validator:
    def __init__(self, output):
        self.output = output
        self.children = []
        self.started = time.monotonic()
        self.result = {"schema_version": 1, "kind": "interruption_validation", "status": "running",
                       "started_at": now(), "started_monotonic": self.started, "checks": [], "errors": [],
                       "limitations": ["Results apply to this isolated installation and these interruption points.",
                                       "SIGKILL cannot run CLI cleanup; its last checkpoint must remain incomplete."]}

    def save(self):
        runner.write_json(self.output / "validation.json", self.result)

    def check(self, name, condition, details):
        self.result["checks"].append({"name": name, "ok": bool(condition), "wall_time": now(),
                                      "monotonic": time.monotonic(), "details": details})
        self.save()
        if not condition:
            raise ValidationFailure(name)

    def launch(self, scenario):
        trial = self.output / f"{scenario}-interrupted.json"
        # No pipe buffers: verbose incident output cannot block runner exit.
        with (self.output / f"{scenario}.stdout.txt").open("x", encoding="utf-8") as stdout, \
                (self.output / f"{scenario}.stderr.txt").open("x", encoding="utf-8") as stderr:
            child = subprocess.Popen([*CLI, "run", scenario, "--duration", "12", "--output", str(trial)],
                                     cwd="/opt/lsrp", env=ENV, stdout=stdout, stderr=stderr,
                                     start_new_session=True)
        self.children.append(child)
        return child, trial

    def checkpoint(self, child, path, timeout=20):
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            try:
                latest = read_record(path)
            except (OSError, ValueError):
                pass
            if (latest and latest.get("record_status") == "running"
                    and (latest.get("injection") or {}).get("confirmed") is True
                    and latest.get("samples")):
                return latest
            if child.poll() is not None:
                raise ValidationFailure(f"Runner exited before an injected sample checkpoint: returncode={child.returncode}; record={latest}")
            time.sleep(.1)
        raise ValidationFailure(f"No confirmed injection sample checkpoint within {timeout}s: {path}")

    def graceful_interruption(self):
        child, path = self.launch("hang")
        checkpoint = self.checkpoint(child, path)
        runner.write_json(self.output / "hang-before-sigterm.json", checkpoint)
        self.check("hang injection checkpoint exists", True,
                   {"runner_pid": child.pid, "samples": len(checkpoint["samples"]), "trial_path": str(path)})
        overlap = subprocess.run([*CLI, "start"], cwd="/opt/lsrp", env=ENV,
                                 capture_output=True, text=True, timeout=10, check=False)
        self.check("overlapping start refused by exclusive lab lock",
                   overlap.returncode != 0 and "Another LSRP operation is running" in overlap.stderr,
                   {"returncode": overlap.returncode, "stdout": overlap.stdout, "stderr": overlap.stderr})
        signalled_at = {"wall_time": now(), "monotonic": time.monotonic(), "runner_pid": child.pid}
        child.send_signal(signal.SIGTERM)
        exit_code = child.wait(timeout=25)
        record = read_record(path)
        self.check("SIGTERM runner exits nonzero with completed failed record and verified cleanup",
                   exit_code != 0 and record.get("record_status") == "complete"
                   and record.get("outcome") == "failed" and record.get("cleanup", {}).get("ok") is True
                   and "SIGTERM" in record.get("error", ""),
                   {**signalled_at, "returncode": exit_code, "record_status": record.get("record_status"),
                    "outcome": record.get("outcome"), "error": record.get("error"),
                    "cleanup_ok": record.get("cleanup", {}).get("ok")})
        checks = runner.wait_ready()
        tail = checks[-3:]
        ids = [identity(p) for p in tail]
        self.check("SIGTERM cleanup restores three consistent normal functional responses",
                   len(tail) == 3 and all(p.get("ok") and p.get("identity", {}).get("mode") == "normal" for p in tail)
                   and ids[0] is not None and len(set(ids)) == 1,
                   {"functional_checks": checks})

    def independent_recovery(self):
        child, path = self.launch("collision")
        checkpoint = self.checkpoint(child, path)
        runner.write_json(self.output / "collision-before-sigkill.json", checkpoint)
        timer_before_kill = properties(lab.TIMER, ["LastTriggerUSecMonotonic", "LastTriggerUSec", "ActiveState", "SubState"])
        self.check("collision injection checkpoint exists", True,
                   {"runner_pid": child.pid, "samples": len(checkpoint["samples"]), "trial_path": str(path),
                    "timer_before_kill": timer_before_kill})
        killed = time.monotonic()
        kill_wall = now()
        child.kill()
        exit_code = child.wait(timeout=5)
        record = read_record(path)
        self.check("SIGKILL preserves incomplete inconclusive record without claiming cleanup",
                   exit_code == -signal.SIGKILL and record.get("record_status") == "running"
                   and record.get("outcome") == "inconclusive" and record.get("cleanup", {}).get("ok") is False
                   and not record.get("recovery", {}).get("sustained"),
                   {"runner_pid": child.pid, "wall_time": kill_wall, "monotonic": killed,
                    "returncode": exit_code, "record_status": record.get("record_status"),
                    "outcome": record.get("outcome"), "cleanup": record.get("cleanup")})
        # Do not call start/cleanup/control while observing: recovery must occur
        # through the deadline timer that the now-dead runner armed beforehand.
        deadline = killed + 40
        observations = []
        streak = []
        timer = recovery = helper = {}
        recovered = False
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            request = observe.probe(timeout=min(.4, max(.01, remaining)))
            current = identity(request)
            valid = request.get("ok") and request.get("identity", {}).get("mode") == "normal" and current is not None
            if valid:
                streak = (streak + [request]) if streak and identity(streak[-1]) == current else [request]
            else:
                streak = []
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            helper = properties(lab.COLLISION, ["MainPID", "ActiveState", "SubState"], timeout=min(2, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            recovery = properties(lab.RECOVER, ["ExecMainStartTimestampMonotonic", "ExecMainExitTimestampMonotonic",
                                               "ExecMainStatus", "Result", "ActiveState"], timeout=min(2, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            timer = properties(lab.TIMER, ["LastTriggerUSecMonotonic", "LastTriggerUSec", "ActiveState", "SubState"],
                               timeout=min(2, remaining))
            observations.append({"wall_time": now(), "monotonic": time.monotonic(), "probe": request,
                                 "helper": helper, "recovery": recovery, "timer": timer})
            # systemctl formats this timer property as a duration (for example
            # '7min 29.885593s'), while the ExecMain timestamp remains numeric.
            # Observe a changed nonzero trigger without parsing display units.
            trigger = timer.get("LastTriggerUSecMonotonic")
            newly_triggered = (trigger not in (None, "", "0", "0s", "0us", "n/a")
                               and trigger != timer_before_kill.get("LastTriggerUSecMonotonic"))
            recovery_start = integer_property(recovery, "ExecMainStartTimestampMonotonic")
            independently_triggered = (newly_triggered
                                       and recovery_start is not None and recovery_start / 1_000_000 > killed)
            helper_inactive = helper.get("ActiveState") == "inactive" and integer_property(helper, "MainPID") == 0
            if independently_triggered and helper_inactive and len(streak) >= 3:
                recovered = True
                break
            time.sleep(min(.2, max(0, deadline - time.monotonic())))
        observation_elapsed = time.monotonic() - killed
        runner.write_json(self.output / "independent-recovery-observations.json",
                          {"schema_version": 1, "kill_monotonic": killed, "observations": observations})
        self.check("independent timer restores normal service and removes helper within 40 seconds",
                   recovered and observation_elapsed <= 40,
                   {"elapsed_s": observation_elapsed, "consecutive_normal_responses": len(streak),
                    "last_three_identities": [identity(p) for p in streak[-3:]],
                    "timer_before_kill": timer_before_kill, "timer": timer, "recovery": recovery, "helper": helper,
                    "observation_path": str(self.output / "independent-recovery-observations.json")})
        # Validate that observing recovery did not rewrite the killed runner's claim.
        after = read_record(path)
        self.check("independent recovery leaves killed trial claim incomplete",
                   after == record, {"record_status": after.get("record_status"), "outcome": after.get("outcome")})

    def stop_owned_children(self):
        for child in self.children:
            if child.poll() is None:
                try:
                    child.send_signal(signal.SIGTERM)
                except ProcessLookupError:
                    child.wait(timeout=5)
                    continue
                try:
                    child.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def interrupted(signum, _frame):
    raise KeyboardInterrupt("Validator interrupted by " + signal.Signals(signum).name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Fresh directory for evidence; its parent must exist")
    args = parser.parse_args(argv)
    # Authorization is tied to the installed fixed lab before any lab mutation.
    marker = lab.guard()
    with lab.locked():
        pass
    output = args.output.absolute()
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    validator = Validator(output)
    validator.result["installation"] = {"install_id": marker["install_id"], "unit": marker["unit"], "port": marker["port"]}
    validator.result["environment"] = lab.environment()
    validator.save()
    previous_handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous_handlers:
        signal.signal(sig, interrupted)
    try:
        validator.graceful_interruption()
        validator.independent_recovery()
    except (Exception, KeyboardInterrupt) as exc:
        validator.result["errors"].append(str(exc) or type(exc).__name__)
    finally:
        for sig in previous_handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            validator.stop_owned_children()
            # The lock prevents cleanup from interfering with another operation.
            with lab.locked():
                cleanup = lab.cleanup()
                if cleanup["ok"]:
                    cleanup["functional_checks"] = runner.wait_ready()
                validator.result["final_cleanup"] = cleanup
                if not cleanup["ok"]:
                    validator.result["errors"].append("Final cleanup was incomplete")
        except Exception as exc:
            validator.result["errors"].append("Final cleanup: " + str(exc))
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
    validator.result["status"] = "passed" if not validator.result["errors"] else "failed"
    validator.result["ended_at"] = now()
    validator.result["elapsed_s"] = time.monotonic() - validator.started
    validator.save()
    print(json.dumps({"status": validator.result["status"], "checks": len(validator.result["checks"]),
                      "errors": validator.result["errors"], "evidence": str(output / "validation.json")}, indent=2))
    return 0 if validator.result["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (lab.LabError, OSError, ValueError) as exc:
        print(f"interruption validation: {exc}", file=sys.stderr)
        raise SystemExit(2)
