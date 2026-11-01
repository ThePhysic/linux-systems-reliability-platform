"""Terminal interface for the fixed Linux reliability lab."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from . import __version__, lab, observe, report, runner, stats


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="lsrp", description="Linux Systems & Reliability Platform: evidence-driven, bounded service experiments.")
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command", required=True)
    for name in ["doctor", "status"]:
        p = sub.add_parser(name)
        p.add_argument("--json", action="store_true")
    for name in ["start", "stop", "cleanup"]:
        sub.add_parser(name)
    p = sub.add_parser("run")
    p.add_argument("scenario", choices=runner.SCENARIOS)
    p.add_argument("--duration", type=float, default=4)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("suite")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--duration", type=float, default=4)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("overhead")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--duration", type=float, default=4)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("report")
    p.add_argument("file", type=Path)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("compare")
    p.add_argument("paths", nargs="+", type=Path)
    p.add_argument("--json", action="store_true")
    return root


def emit(value: dict) -> None:
    print(json.dumps(value, indent=2, allow_nan=False))


def read_trial(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not data.get("trial_id"):
        raise lab.LabError(f"Not a schema-v1 trial: {path}")
    if data.get("scenario") not in runner.SCENARIOS or not isinstance(data.get("samples"), list):
        raise lab.LabError(f"Invalid trial structure: {path}")
    if not isinstance(data["trial_id"], str) or ("outcome" in data and data["outcome"] not in ["recovered", "failed", "inconclusive"]):
        raise lab.LabError(f"Invalid trial identity/outcome: {path}")
    for key in ["metrics", "injection", "cleanup", "environment", "parameters"]:
        if key in data and not isinstance(data[key], dict):
            raise lab.LabError(f"Invalid {key} object: {path}")
    for key in ["events", "baseline", "limitations"]:
        if key in data and not isinstance(data[key], list):
            raise lab.LabError(f"Invalid {key} array: {path}")
    for sample in data.get("baseline", []) + data["samples"]:
        if not isinstance(sample, dict):
            raise lab.LabError(f"Invalid snapshot: {path}")
        for key in ["probe", "unit", "process", "cgroup", "socket", "journal"]:
            if key in sample and not isinstance(sample[key], dict):
                raise lab.LabError(f"Invalid snapshot {key}: {path}")
        for key in ["monotonic", "completed_monotonic"]:
            if key in sample and (type(sample[key]) not in [int, float] or not math.isfinite(sample[key])):
                raise lab.LabError(f"Invalid snapshot timestamp: {path}")
    return data


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = {"ok": True, "environment": lab.environment(), "error": None}
            try:
                marker = lab.guard(require_root=False)
                result["installation"] = {"schema_version": marker["schema_version"], "unit": marker["unit"], "port": marker["port"]}
            except lab.LabError as exc:
                result.update(ok=False, error=str(exc))
            if args.json:
                emit(result)
            else:
                print("Lab preconditions passed." if result["ok"] else result["error"])
                print(json.dumps(result["environment"], indent=2))
            return 0 if result["ok"] else 1
        if args.command == "status":
            result = observe.snapshot()
            if args.json:
                emit(result)
            else:
                print(f"Functional work check: {'PASS' if result['probe'].get('ok') else 'FAIL'}")
                for key in ["unit", "process", "cgroup", "socket", "journal"]:
                    print(f"{key}: {result[key]['status']} ({result[key]['source']})")
            return 0 if result["probe"].get("ok") else 1
        if args.command in ["start", "stop", "cleanup"]:
            with lab.locked():
                if args.command == "start":
                    lab.start()
                    runner.wait_ready()
                    print("Service passed three consecutive functional work checks.")
                else:
                    result = lab.cleanup(restart=args.command == "cleanup")
                    if args.command == "cleanup" and result["ok"]:
                        runner.wait_ready()
                    emit(result)
                    return 0 if result["ok"] else 1
            return 0
        if args.command == "run":
            trial = runner.run_trial(args.scenario, args.duration, args.output)
            print(report.render(trial))
            print(f"Trial saved: {args.output}")
            return 0 if trial["outcome"] == "recovered" else 1
        if args.command == "suite":
            result = runner.suite(args.trials, args.duration, args.output)
            emit(result)
            return 0 if result["ok"] else 1
        if args.command == "overhead":
            emit(runner.overhead(args.duration, args.trials, args.output))
            return 0
        if args.command == "report":
            trial = read_trial(args.file)
            if args.json:
                emit(trial)
            else:
                print(report.render(trial))
            return 0
        if args.command == "compare":
            files = []
            for path in args.paths:
                if path.is_dir():
                    # summary/overhead are not trial records; malformed trial files remain errors.
                    files.extend(p for p in sorted(path.glob("*.json")) if p.name != "summary.json")
                else:
                    files.append(path)
            records, seen = [], set()
            for path in files:
                trial = read_trial(path)
                if trial["trial_id"] in seen:
                    raise lab.LabError(f"Duplicate trial ID: {trial['trial_id']}")
                seen.add(trial["trial_id"])
                records.append(trial)
            if not records:
                raise lab.LabError("No trial JSON records found.")
            result = stats.summarize(records)
            if args.json:
                emit(result)
            else:
                print(json.dumps(result, indent=2))
            return 0
    except (lab.LabError, OSError, ValueError, KeyError) as exc:
        print(f"lsrp: {exc}", file=sys.stderr)
        return 2
    return 2
