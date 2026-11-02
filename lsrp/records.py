"""Validate saved incident records before rendering or comparing their evidence.

Optional fields may be absent in interrupted checkpoints. Validation never fills
missing observations, repairs malformed values, or changes the loaded record.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

MAX_DEPTH = 64
SCENARIOS = ("crash", "hang", "cpu", "collision")


class InvalidRecord(ValueError):
    """A saved record is malformed; its location is suitable for CLI output."""


def _at(parent, key):
    if isinstance(key, int):
        return f"{parent}[{key}]"
    if key.isidentifier():
        return f"{parent}.{key}" if parent else key
    return f"{parent}[{json.dumps(key)}]"


def _fail(path, expected):
    raise InvalidRecord(f"{path or '$'}: expected {expected}")


def _object(value, path):
    if not isinstance(value, dict):
        _fail(path, "an object")


def _array(value, path):
    if not isinstance(value, list):
        _fail(path, "an array")


def _text(value, path, nullable=False):
    if not isinstance(value, str) and not (nullable and value is None):
        _fail(path, "a string" + (" or null" if nullable else ""))


def _boolean(value, path):
    if type(value) is not bool:
        _fail(path, "a boolean")


def _number(value, path, nullable=False, integer=False, minimum=None):
    if nullable and value is None:
        return
    valid = type(value) is int if integer else type(value) in (int, float)
    if (not valid or abs(value) > sys.float_info.max or not math.isfinite(value)
            or (minimum is not None and value < minimum)):
        description = "a finite integer" if integer else "a finite number"
        if minimum is not None:
            description += f" >= {minimum}"
        _fail(path, description + (" or null" if nullable else ""))


def _fields(obj, path, names, check, **kwargs):
    for name in names:
        if name in obj:
            check(obj[name], _at(path, name), **kwargs)


def _strings(value, path):
    _array(value, path)
    for index, item in enumerate(value):
        _text(item, _at(path, index))


def _json_tree(value):
    # Iterative traversal bounds validation depth independently of Python's
    # recursion limit and checks extension fields as well as known fields.
    pending = [(value, "", 0)]
    while pending:
        item, path, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise InvalidRecord(f"{path or '$'}: nesting exceeds {MAX_DEPTH} levels")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    _fail(path, "string object keys")
                pending.append((child, _at(path, key), depth + 1))
        elif isinstance(item, list):
            pending.extend((child, _at(path, i), depth + 1) for i, child in enumerate(item))
        elif type(item) in (int, float):
            _number(item, path)
        elif item is not None and type(item) not in (str, bool):
            _fail(path, "a JSON value")


def _identity(value, path):
    if value is None:
        return
    _object(value, path)
    _fields(value, path, ("pid", "start_ticks", "ppid", "uid"), _number,
            nullable=True, integer=True, minimum=0)
    _fields(value, path, ("boot_id", "instance", "service", "mode"), _text, nullable=True)


def _metadata(value, path):
    _object(value, path)
    _fields(value, path, ("source", "collected_at"), _text)
    _fields(value, path, ("error",), _text, nullable=True)
    if "status" in value and value["status"] not in ("ok", "unavailable", "error"):
        _fail(_at(path, "status"), "ok, unavailable or error")


def _probe(value, path):
    _object(value, path)
    _fields(value, path, ("ok",), _boolean)
    _fields(value, path, ("source", "collected_at"), _text)
    _fields(value, path, ("error", "error_kind"), _text, nullable=True)
    _fields(value, path, ("monotonic", "completed_monotonic"), _number, minimum=0)
    _fields(value, path, ("latency_ms",), _number, nullable=True, minimum=0)
    _fields(value, path, ("http_status",), _number, nullable=True, integer=True, minimum=0)
    _fields(value, path, ("identity",), _identity)
    verification = value.get("identity_verification")
    if verification is not None:
        where = _at(path, "identity_verification")
        _metadata(verification, where)
        if "data" in verification:
            _identity(verification["data"], _at(where, "data"))


def _integer_text(value, path):
    if type(value) is int:
        _number(value, path, integer=True, minimum=0)
    elif not (isinstance(value, str) and value.isascii() and value.isdecimal()
              and len(value) <= 20):
        _fail(path, "a nonnegative integer or decimal integer string")


def _journal(value, path):
    _fields(value, path, ("visibility_warning",), _text, nullable=True)
    if "entries" not in value:
        return
    entries, where = value["entries"], _at(path, "entries")
    _array(entries, where)
    for index, entry in enumerate(entries):
        at = _at(where, index)
        _object(entry, at)
        for key, item in entry.items():
            if key in ("__MONOTONIC_TIMESTAMP", "__REALTIME_TIMESTAMP"):
                _integer_text(item, _at(at, key))
            elif key == "MESSAGE" and isinstance(item, list):
                # journalctl represents binary fields as arrays of byte values.
                for i, byte in enumerate(item):
                    _number(byte, _at(_at(at, key), i), integer=True, minimum=0)
                    if byte > 255:
                        _fail(_at(_at(at, key), i), "a byte between 0 and 255")
            else:
                _text(item, _at(at, key), nullable=True)


def _cgroup(value, path):
    _fields(value, path, ("path",), _text, nullable=True)
    if value.get("generation") is not None:
        where = _at(path, "generation")
        _object(value["generation"], where)
        _fields(value["generation"], where, ("device", "inode"), _number,
                nullable=True, integer=True, minimum=0)
    if "files" not in value:
        return
    files, where = value["files"], _at(path, "files")
    _object(files, where)
    for name, counter in files.items():
        at = _at(where, name)
        _metadata(counter, at)
        observed = counter.get("value")
        if observed is None:
            continue
        if name in ("cpu.stat", "memory.events"):
            _object(observed, _at(at, "value"))
            for key, number in observed.items():
                _number(number, _at(_at(at, "value"), key), integer=True, minimum=0)
        elif name == "cpu.max":
            _text(observed, _at(at, "value"))
        elif name in ("memory.current", "memory.max", "pids.current", "pids.max") and observed != "max":
            _number(observed, _at(at, "value"), integer=True, minimum=0)


def _evidence(value, path, kind):
    _metadata(value, path)
    data = value.get("data")
    if data is None:
        return
    at = _at(path, "data")
    _object(data, at)
    if kind == "unit":
        for key, item in data.items():
            if key == "NRestarts" and item is not None:
                _integer_text(item, _at(at, key))
            else:
                _text(item, _at(at, key), nullable=True)
    elif kind == "process":
        _identity(data, at)
        _fields(data, at, ("comm", "state", "limits", "cgroup"), _text, nullable=True)
        _fields(data, at, ("cpu_seconds", "uptime_s"), _number, nullable=True, minimum=0)
        _fields(data, at, ("rss_bytes", "fd_count"), _number, nullable=True, integer=True, minimum=0)
        if data.get("children") is not None:
            where = _at(at, "children")
            _array(data["children"], where)
            for i, child in enumerate(data["children"]):
                _number(child, _at(where, i), integer=True, minimum=0)
        if "issues" in data:
            _object(data["issues"], _at(at, "issues"))
            for key, issue in data["issues"].items():
                _text(issue, _at(_at(at, "issues"), key))
    elif kind == "cgroup":
        _cgroup(data, at)
    elif kind == "socket":
        _fields(data, at, ("listeners",), _strings)
        _fields(data, at, ("count",), _number, integer=True, minimum=0)
        _fields(data, at, ("ownership_visibility",), _text)
    elif kind == "journal":
        _journal(data, at)


def _snapshot(value, path):
    _object(value, path)
    _fields(value, path, ("wall_time",), _text)
    _fields(value, path, ("monotonic", "completed_monotonic"), _number, minimum=0)
    _fields(value, path, ("probe",), _probe)
    for name in ("unit", "process", "cgroup", "socket", "journal"):
        if name in value:
            _evidence(value[name], _at(path, name), name)


def _objects(value, path, validate=None):
    _array(value, path)
    for i, item in enumerate(value):
        at = _at(path, i)
        _object(item, at)
        if validate is not None:
            validate(item, at)


def _event(value, path):
    _fields(value, path, ("wall_time", "phase", "message"), _text)
    _fields(value, path, ("monotonic",), _number, minimum=0)


def _cleanup(value, path):
    _object(value, path)
    _fields(value, path, ("ok",), _boolean)
    _fields(value, path, ("errors",), _strings)
    if "verification" in value:
        where = _at(path, "verification")
        _object(value["verification"], where)
        for key, state in value["verification"].items():
            _text(state, _at(where, key), nullable=True)
    if "functional_checks" in value:
        _objects(value["functional_checks"], _at(path, "functional_checks"), _probe)


def _metrics(value, path):
    _object(value, path)
    for key, item in value.items():
        at = _at(path, key)
        if key.endswith("_s") or key in ("latency_degradation_threshold_ms", "probe_success_ratio"):
            _number(item, at, nullable=True, minimum=0)
        elif key in ("probe_count", "failed_probe_count", "process_identity_count"):
            _number(item, at, integer=True, minimum=0)
        elif key in ("baseline_latency_ms", "trial_success_latency_ms"):
            _object(item, at)
            _fields(item, at, ("n", "missing"), _number, integer=True, minimum=0)
            _fields(item, at, ("median", "min", "max", "p95"), _number, nullable=True, minimum=0)
        elif key == "failure_onset_bounds_monotonic" and item is not None:
            _array(item, at)
            if len(item) != 2:
                _fail(at, "two onset bounds")
            for i, bound in enumerate(item):
                _number(bound, _at(at, i), nullable=True, minimum=0)
        elif key == "cpu_counter_delta" and item is not None:
            _object(item, at)
            for counter, number in item.items():
                _number(number, _at(at, counter), integer=True, minimum=0)
        elif key == "timing_reference":
            _text(item, at)


def validate_trial(data):
    """Reject malformed supplied fields while preserving optional/missing data."""
    _json_tree(data)
    _object(data, "")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        _fail("schema_version", "the integer 1")
    _text(data.get("trial_id"), "trial_id")
    if not data["trial_id"].strip():
        _fail("trial_id", "a nonempty string")
    if data.get("scenario") not in SCENARIOS:
        _fail("scenario", "crash, hang, cpu or collision")
    if "outcome" in data and data["outcome"] not in ("recovered", "failed", "inconclusive"):
        _fail("outcome", "recovered, failed or inconclusive")
    _fields(data, "", ("record_status", "error"), _text)
    _fields(data, "", ("incident_observed", "functional_failure_observed", "throttling_observed"), _boolean)
    _fields(data, "", ("environment", "parameters"), _object)
    _fields(data, "", ("limitations",), _strings)
    _fields(data, "", ("metrics",), _metrics)
    _fields(data, "", ("cleanup",), _cleanup)
    if "baseline" in data:
        _objects(data["baseline"], "baseline", _snapshot)
    _objects(data.get("samples"), "samples", _snapshot)
    if "events" in data:
        _objects(data["events"], "events", _event)
    if "injection" in data:
        injection = data["injection"]
        _object(injection, "injection")
        _fields(injection, "injection", ("requested_at", "action"), _text)
        _fields(injection, "injection", ("confirmed",), _boolean)
        _fields(injection, "injection", ("monotonic", "confirmed_monotonic"), _number, minimum=0)
        if "details" in injection:
            details = injection["details"]
            _object(details, "injection.details")
            _fields(details, "injection.details", ("before_identity",), _identity)
    if "diagnosis" in data:
        diagnosis = data["diagnosis"]
        _object(diagnosis, "diagnosis")
        _fields(diagnosis, "diagnosis", ("symptoms", "limitations"), _strings)
        for name in ("evidence", "hypotheses", "confirmed_mechanisms"):
            if name in diagnosis:
                _objects(diagnosis[name], _at("diagnosis", name))
    if "recovery" in data:
        recovery = data["recovery"]
        _object(recovery, "recovery")
        _fields(recovery, "recovery", ("sustained",), _boolean)
        _fields(recovery, "recovery", ("first_success_monotonic", "confirmed_monotonic"),
                _number, nullable=True, minimum=0)
        _fields(recovery, "recovery", ("consecutive_successes",), _number, integer=True, minimum=0)
        for name in ("provisional_segments", "relapses"):
            if name in recovery:
                _objects(recovery[name], _at("recovery", name))
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRecord(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def load_trial(path: Path):
    path = Path(path)
    try:
        # JSON's default NaN/Infinity decoding is checked with exact field paths
        # by validate_trial, including numeric overflow such as 1e999.
        try:
            data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        except RecursionError as exc:
            raise InvalidRecord(f"$: JSON nesting exceeds the supported {MAX_DEPTH} levels") from exc
        except json.JSONDecodeError as exc:
            raise InvalidRecord(f"$: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
        except ValueError as exc:
            raise InvalidRecord(f"$: invalid JSON: {exc}") from exc
        return validate_trial(data)
    except InvalidRecord as exc:
        raise InvalidRecord(f"{path}: {exc}") from exc
