"""Bounded, serialized experiments and reproducible trial artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import statistics
import tempfile
import time
import uuid

from . import lab, observe, report, stats

SCENARIOS = ("crash", "hang", "cpu", "collision")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with exclusive temporary name; never follow an existing output symlink.
    fd, name = tempfile.mkstemp(prefix=".lsrp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def parameters(duration: float, trials: int = 1) -> None:
    if not math.isfinite(duration) or not 2 <= duration <= 12:
        raise lab.LabError("Duration must be finite and between 2 and 12 seconds.")
    if not 1 <= trials <= 30:
        raise lab.LabError("Trials must be between 1 and 30.")


def wait_ready(timeout: float = 12) -> list[dict]:
    deadline, good, observations = time.monotonic() + timeout, 0, []
    previous_identity = None
    while time.monotonic() < deadline:
        p = observe.snapshot()["probe"]
        observations.append(p)
        identity = p.get("identity") or {}
        current_identity = tuple(identity.get(k) for k in ["boot_id", "pid", "start_ticks", "instance"])
        valid = p.get("ok") and identity.get("mode") == "normal" and all(v is not None for v in current_identity)
        good = good + 1 if valid and current_identity == previous_identity else 1 if valid else 0
        previous_identity = current_identity if valid else None
        if good >= 3:
            return observations
        time.sleep(.15)
    raise lab.LabError("Service did not pass three consecutive functional checks.")


def _event(trial: dict, phase: str, message: str) -> None:
    trial["events"].append({"wall_time": now(), "monotonic": time.monotonic(), "phase": phase, "message": message})


class Interrupted(lab.LabError):
    pass


def _interrupt(signum, _frame):
    raise Interrupted(f"Interrupted by {signal.Signals(signum).name}")


def run_trial(scenario: str, duration: float, output: Path) -> dict:
    parameters(duration)
    if scenario not in SCENARIOS:
        raise lab.LabError("Unknown scenario.")
    trial = {"schema_version": 1, "trial_id": str(uuid.uuid4()), "scenario": scenario,
             "environment": {}, "parameters": {"duration_s": duration, "poll_interval_s": .2,
             "probe_timeout_s": .4, "recovery_successes": 3}, "baseline": [], "samples": [],
             "injection": {}, "events": [], "cleanup": {"ok": False, "errors": ["not attempted"]},
             "outcome": "inconclusive", "metrics": {}, "limitations": [
                 "Single-node synthetic workload in a disposable lab; results do not establish production availability.",
                 "Snapshots are sequential, not atomic; collection/probe overhead changes sampling resolution.",
                 "Injection dispatch is an experiment timing reference, not exact failure onset."]}
    with lab.locked():
        previous_handlers = {s: signal.getsignal(s) for s in [signal.SIGINT, signal.SIGTERM]}
        for s in previous_handlers:
            signal.signal(s, _interrupt)
        failure = None
        touched = False
        try:
            trial["environment"] = lab.environment()
            _event(trial, "precondition", "Verified fixed installation, non-root service identity and exclusive lab lock.")
            touched = True
            initial = lab.cleanup()
            if not initial["ok"]:
                raise lab.LabError(f"Cannot establish clean baseline: {initial['errors']}")
            wait_ready()
            for _ in range(4):
                trial["baseline"].append(observe.snapshot())
                time.sleep(.2)
            if not all(s["probe"].get("ok") for s in trial["baseline"]):
                raise lab.LabError("Baseline work probes did not all succeed.")
            owner = lab.identity()
            props = lab.properties()
            cgroup = Path("/sys/fs/cgroup") / props["ControlGroup"].lstrip("/")
            quota = (cgroup / "cpu.max").read_text().split()
            memory = (cgroup / "memory.max").read_text().strip()
            if quota[0] == "max" or int(quota[0]) / int(quota[1]) > .25 or memory == "max" or int(memory) > 128 * 1024**2:
                raise lab.LabError("Expected enforced CPU/memory boundaries are absent.")
            # Independent timer outlives an interrupted/killed CLI process.
            lab.systemctl("restart", lab.TIMER)
            trial["injection"] = {"requested_at": now(), "monotonic": time.monotonic(), "action": scenario,
                                   "confirmed": False, "details": {"before_identity": owner}}
            _event(trial, "injection", f"Dispatching bounded {scenario} experiment; ground truth is separate from diagnosis.")
            if scenario == "crash":
                lab.systemctl("kill", "--kill-whom=main", "--signal=SIGKILL", lab.UNIT)
            elif scenario in ["hang", "cpu"]:
                trial["injection"]["details"]["control"] = lab.control(scenario, duration)
            else:
                lab.systemctl("stop", lab.UNIT)
                lab.systemctl("start", lab.COLLISION)
                helper = lab.properties(lab.COLLISION)
                if helper.get("ActiveState") != "active":
                    raise lab.LabError("Collision helper did not start.")
                # Bind readiness, not merely process activation, is a precondition.
                import socket
                deadline = time.monotonic() + 2
                while True:
                    with socket.socket() as sock:
                        sock.settimeout(.2)
                        ready = sock.connect_ex(("127.0.0.1", lab.PORT)) == 0
                    if ready:
                        break
                    if time.monotonic() >= deadline:
                        raise lab.LabError("Collision helper failed to acquire its port.")
                    time.sleep(.05)
                lab.systemctl("start", lab.UNIT, check=False)
            trial["injection"]["confirmed"] = True
            trial["injection"]["confirmed_monotonic"] = time.monotonic()
            release_at = trial["injection"]["monotonic"] + duration
            deadline = release_at + 14
            released, good = False, 0
            while time.monotonic() < deadline:
                sample = observe.snapshot()
                trial["samples"].append(sample)
                if not released and time.monotonic() >= release_at:
                    if scenario == "collision":
                        lab.systemctl("stop", lab.COLLISION)
                        lab.start()
                    elif scenario in ["hang", "cpu"]:
                        lab.control("normal")
                    released = True
                    _event(trial, "restoration", "Fault interval ended; validate sustained end-to-end recovery.")
                    good = 0
                elif released:
                    p = sample["probe"]
                    mode = (p.get("identity") or {}).get("mode", "normal")
                    good = good + 1 if p.get("ok") and mode == "normal" else 0
                    if good >= 4:
                        break
                time.sleep(.2)
            _event(trial, "observation_end", "Bounded observation window completed.")
        except (Exception, KeyboardInterrupt) as exc:
            failure = str(exc) or type(exc).__name__
            trial["error"] = failure
            _event(trial, "error", failure)
        finally:
            # A second SIGINT/SIGTERM must not skip resource restoration.
            for s in previous_handlers:
                signal.signal(s, signal.SIG_IGN)
            try:
                if touched:
                    trial["cleanup"] = lab.cleanup()
                    try:
                        trial["cleanup"]["functional_checks"] = wait_ready()
                    except Exception as exc:
                        trial["cleanup"]["ok"] = False
                        trial["cleanup"]["errors"].append(str(exc))
                _event(trial, "cleanup", "Cleanup verified." if trial["cleanup"]["ok"] else "Cleanup incomplete.")
                trial.update(report.analyze(trial))
                trial["outcome"] = ("recovered" if trial.get("incident_observed") and trial.get("recovery", {}).get("sustained")
                                    else "failed" if trial.get("incident_observed") else "inconclusive")
                if failure or not trial["cleanup"]["ok"]:
                    trial["outcome"] = "failed"
                write_json(output, trial)
            finally:
                for s, handler in previous_handlers.items():
                    signal.signal(s, handler)
    return trial


def suite(trials: int, duration: float, output: Path) -> dict:
    parameters(duration, trials)
    output = Path(output)
    records = []
    for scenario in SCENARIOS:
        for _ in range(trials):
            name = f"{scenario}-{uuid.uuid4()}.json"
            record = run_trial(scenario, duration, output / name)
            records.append(record)
            if record.get("error") or not record["cleanup"]["ok"]:
                aggregate = stats.summarize(records)
                write_json(output / "summary.json", aggregate)
                raise lab.LabError(f"Suite stopped after {name}: {record.get('error', 'cleanup failed')}")
    result = stats.summarize(records)
    write_json(output / "summary.json", result)
    return {"summary": result, "ok": all(t["outcome"] == "recovered" for t in records)}


def overhead(duration: float, trials: int, output: Path) -> dict:
    """Paired, alternating-order closed-loop workloads with/without observation."""
    import threading
    parameters(duration, trials)
    records = []
    with lab.locked():
        lab.start()
        wait_ready()
        for index in range(trials):
            pair = {}
            for enabled in ([False, True] if index % 2 == 0 else [True, False]):
                wait_ready()
                stop = threading.Event()
                collector = {"count": 0, "errors": []}
                def collect():
                    while not stop.is_set():
                        try:
                            observe.snapshot()
                            collector["count"] += 1
                        except Exception as exc:
                            collector["errors"].append(str(exc))
                        stop.wait(.2)
                thread = threading.Thread(target=collect, daemon=True)
                cpu0, start_time, probes = time.process_time(), time.monotonic(), []
                if enabled:
                    thread.start()
                try:
                    while time.monotonic() - start_time < duration:
                        probes.append(observe.probe())
                finally:
                    stop.set()
                    if enabled:
                        thread.join(timeout=10)
                        if thread.is_alive():
                            raise lab.LabError("Overhead collector did not stop.")
                elapsed = time.monotonic() - start_time
                latencies = [p["latency_ms"] for p in probes if p.get("ok")]
                pair["observed" if enabled else "baseline"] = {
                    "elapsed_s": elapsed, "requests": len(probes), "successes": len(latencies),
                    "success_ratio": len(latencies) / len(probes) if probes else None,
                    "successful_requests_per_s": len(latencies) / elapsed,
                    "median_latency_ms": statistics.median(latencies) if latencies else None,
                    "runner_cpu_s": time.process_time() - cpu0,
                    "snapshots": collector["count"], "collector_errors": collector["errors"]}
            base = pair["baseline"]["successful_requests_per_s"]
            pair["throughput_change_percent"] = (100 * (pair["observed"]["successful_requests_per_s"] / base - 1)) if base else None
            records.append(pair)
        result = {"schema_version": 1, "kind": "observation_overhead", "environment": lab.environment(),
                  "wall_time": now(), "duration_s": duration, "trials": records, "n": trials,
                  "limitations": ["Closed-loop single client, synthetic work and fixed 25% CPU quota.",
                      "Collector adds its own functional probe, so this measures combined collection/probe interference.",
                      "runner_cpu_s excludes subprocess CPU; elapsed time includes bounded collector join.",
                      "Alternating order reduces ordering bias; small samples are descriptive, not causal performance claims."]}
        write_json(output, result)
        return result
