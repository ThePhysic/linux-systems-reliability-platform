"""Read-only, source-bearing observations of the one LSRP lab service."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
import uuid

UNIT = "lsrp-lab.service"
PORT = 18765
WORK_RESULT = hashlib.sha256(b"lsrp-reliability-lab").hexdigest()
PROC = Path("/proc")
CGROUP = Path("/sys/fs/cgroup")
SYSTEMCTL = "/usr/bin/systemctl"
JOURNALCTL = "/usr/bin/journalctl"
SS = "/usr/bin/ss"
COMMAND_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C",
               "SYSTEMD_COLORS": "0", "SYSTEMD_PAGER": "cat", "SYSTEMD_PAGERSECURE": "1"}


class NoRedirect(HTTPRedirectHandler):
    """A lab probe must never leave its explicitly recorded loopback target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


UNIT_FIELDS = (
    "Id", "LoadState", "ActiveState", "SubState", "Result", "MainPID",
    "NRestarts", "ExecMainCode", "ExecMainStatus", "ExecMainStartTimestampMonotonic",
    "ExecMainExitTimestampMonotonic", "ActiveEnterTimestampMonotonic", "ControlGroup",
    "InvocationID", "User", "FragmentPath", "CPUQuotaPerSecUSec", "MemoryMax",
    "TasksMax", "LimitNOFILE", "Restart", "RestartUSec",
)


def wall_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def evidence(source, data=None, *, status="ok", error=None):
    return {"source": source, "collected_at": wall_time(), "status": status,
            "data": data, "error": error}


def command(args, timeout=2.0):
    """No shell, no user supplied command fragments, bounded subprocess lifetime."""
    if not args or args[0] not in (SYSTEMCTL, JOURNALCTL, SS):
        raise ValueError("Observer command is not an approved absolute executable")
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          check=False, env=COMMAND_ENV)


def unit_state():
    source = "systemctl show " + UNIT
    try:
        result = command([SYSTEMCTL, "show", UNIT, "--no-pager",
                          "--property=" + ",".join(UNIT_FIELDS)])
        if result.returncode:
            return evidence(source, status="unavailable", error=result.stderr.strip()[:1000])
        data = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if data.get("LoadState") == "not-found":
            return evidence(source, data, status="unavailable", error="Lab unit is not installed")
        return evidence(source, data)
    except (OSError, subprocess.SubprocessError) as exc:
        return evidence(source, status="unavailable", error=str(exc))


def parse_stat(text, clock_ticks, page_size):
    """/proc stat's comm may contain whitespace and parentheses."""
    end = text.rfind(")")
    begin = text.find("(")
    if begin < 1 or end <= begin:
        raise ValueError("Malformed proc stat")
    pid = int(text[:begin].strip())
    fields = text[end + 1:].split()
    if len(fields) < 22:
        raise ValueError("Truncated proc stat")
    return {"pid": pid, "comm": text[begin + 1:end], "state": fields[0],
            "ppid": int(fields[1]), "start_ticks": int(fields[19]),
            "cpu_seconds": (int(fields[11]) + int(fields[12])) / clock_ticks,
            "rss_bytes": int(fields[21]) * page_size}


def process_state(pid):
    source = f"/proc/{pid}/stat,status,limits,fd,cgroup,task/*/children; /proc/uptime; /proc/sys/kernel/random/boot_id"
    if not isinstance(pid, int) or pid <= 0:
        return evidence(source, status="unavailable", error="Unit has no main PID")
    try:
        directory = PROC / str(pid)
        data = parse_stat((directory / "stat").read_text(), os.sysconf("SC_CLK_TCK"),
                          os.sysconf("SC_PAGE_SIZE"))
        data["boot_id"] = (PROC / "sys/kernel/random/boot_id").read_text().strip()
        data["issues"] = {}
        for key, read in (
            ("children", lambda: sorted({int(child) for file in (directory / "task").glob("*/children")
                                          for child in file.read_text().split()})),
            ("uptime_s", lambda: max(0.0, float((PROC / "uptime").read_text().split()[0])
                                       - data["start_ticks"] / os.sysconf("SC_CLK_TCK"))),
            ("fd_count", lambda: len(list((directory / "fd").iterdir()))),
            ("limits", lambda: (directory / "limits").read_text()),
            ("cgroup", lambda: (directory / "cgroup").read_text().strip()),
            ("uid", lambda: int(next(line for line in (directory / "status").read_text().splitlines()
                                    if line.startswith("Uid:")).split()[1])),
        ):
            try:
                data[key] = read()
            except (OSError, ValueError, StopIteration) as exc:
                data[key] = None
                data["issues"][key] = str(exc)
        # Reject a mixed observation if the task disappeared or the PID was reused.
        after = parse_stat((directory / "stat").read_text(), os.sysconf("SC_CLK_TCK"),
                           os.sysconf("SC_PAGE_SIZE"))
        if (data["pid"], data["start_ticks"]) != (after["pid"], after["start_ticks"]):
            return evidence(source, status="unavailable", error="PID identity changed during collection")
        return evidence(source, data)
    except (OSError, ValueError, IndexError, AttributeError) as exc:
        return evidence(source, status="unavailable", error=str(exc))


def _counter_file(text):
    result = {}
    for line in text.splitlines():
        key, value = line.split(maxsplit=1)
        result[key] = int(value)
    return result


def cgroup_state(path):
    source = "cgroup v2 " + str(path)
    if not path or not isinstance(path, str) or not path.startswith("/"):
        return evidence(source, status="unavailable", error="Unit has no cgroup path")
    try:
        root = CGROUP.resolve()
        directory = (root / path.lstrip("/")).resolve()
        if directory == root or root not in directory.parents or ".." in Path(path).parts:
            raise ValueError("Refusing non-service cgroup path")
        before = directory.stat()
        data = {"path": path, "generation": {"device": before.st_dev, "inode": before.st_ino}, "files": {}}
        for name in ("cpu.stat", "cpu.max", "memory.current", "memory.max", "memory.events", "pids.current", "pids.max"):
            filename = directory / name
            try:
                value = filename.read_text().strip()
                if name in ("cpu.stat", "memory.events"):
                    value = _counter_file(value)
                elif name != "cpu.max" and value != "max":
                    value = int(value)
                data["files"][name] = {"status": "ok", "value": value, "error": None,
                                        "source": str(filename), "collected_at": wall_time()}
            except (OSError, ValueError) as exc:
                data["files"][name] = {"status": "unavailable", "value": None, "error": str(exc),
                                        "source": str(filename), "collected_at": wall_time()}
        after = directory.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            return evidence(source, status="unavailable", error="Cgroup generation changed during collection")
        status = "ok" if any(v["status"] == "ok" for v in data["files"].values()) else "unavailable"
        return evidence(source, data, status=status,
                        error=None if status == "ok" else "All cgroup counters unavailable")
    except (OSError, ValueError) as exc:
        return evidence(source, status="unavailable", error=str(exc))


def socket_state():
    source = f"ss -H -ltnp 'sport = :{PORT}'"
    try:
        result = command([SS, "-H", "-ltnp", f"sport = :{PORT}"])
        if result.returncode:
            return evidence(source, status="unavailable", error=result.stderr.strip()[:1000])
        listeners = result.stdout.strip().splitlines()
        return evidence(source, {"listeners": listeners[:16], "count": len(listeners),
                                  "ownership_visibility": "PID owner appears only when ss permits it"})
    except (OSError, subprocess.SubprocessError) as exc:
        return evidence(source, status="unavailable", error=str(exc))


def journal_state():
    source = "journalctl --boot --unit=" + UNIT
    try:
        result = command([JOURNALCTL, "--boot", "--unit=" + UNIT, "--lines=20", "--output=json", "--no-pager"])
        if result.returncode:
            return evidence(source, status="unavailable", error=result.stderr.strip()[:1000])
        entries = []
        allow = {"__REALTIME_TIMESTAMP", "__MONOTONIC_TIMESTAMP", "_BOOT_ID", "_PID", "_SYSTEMD_INVOCATION_ID",
                 "_SYSTEMD_UNIT", "UNIT", "MESSAGE", "PRIORITY", "MESSAGE_ID", "EXIT_CODE", "EXIT_STATUS", "RESULT"}
        for line in result.stdout.splitlines()[-20:]:
            record = json.loads(line)
            entries.append({k: v[:2000] if isinstance(v, str) else v for k, v in record.items() if k in allow})
        error = result.stderr.strip()[:1000] or None
        return evidence(source, {"entries": entries, "visibility_warning": error},
                        status="ok" if not error else "unavailable", error=error)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return evidence(source, status="unavailable", error=str(exc))


def _validate_identity(data):
    if not isinstance(data, dict) or data.get("service") != "lsrp-lab":
        raise ValueError("Response is not the LSRP service")
    for field in ("pid", "start_ticks"):
        if type(data.get(field)) is not int or data[field] <= 0:
            raise ValueError("Invalid service identity: " + field)
    for field in ("boot_id", "instance"):
        if not isinstance(data.get(field), str):
            raise ValueError("Invalid service identity: " + field)
        uuid.UUID(data[field])
    if data.get("mode") not in ("normal", "hang", "cpu"):
        raise ValueError("Invalid service mode")
    if data.get("result") != WORK_RESULT:
        raise ValueError("Work result did not match the known computation")
    return {k: data[k] for k in ("service", "pid", "start_ticks", "boot_id", "instance", "mode")}


def _verify_local_identity(identity):
    """A response's self-reported identity must agree with the local kernel."""
    source = f"/proc/{identity['pid']}/stat; /proc/sys/kernel/random/boot_id"
    try:
        actual = parse_stat((PROC / str(identity["pid"]) / "stat").read_text(),
                            os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE"))
        actual["boot_id"] = (PROC / "sys/kernel/random/boot_id").read_text().strip()
        if any(actual[k] != identity[k] for k in ("pid", "start_ticks", "boot_id")):
            return evidence(source, status="error", error="Response identity disagrees with the local kernel")
        return evidence(source, {k: actual[k] for k in ("pid", "start_ticks", "boot_id")})
    except (OSError, ValueError, AttributeError, IndexError) as exc:
        return evidence(source, status="unavailable", error=str(exc))


def probe(timeout=0.4):
    """An actual computation over loopback, without environmental HTTP proxies."""
    started = time.monotonic()
    result = {"source": f"HTTP GET http://127.0.0.1:{PORT}/work", "collected_at": wall_time(),
              "monotonic": started, "completed_monotonic": None, "ok": False,
              "latency_ms": None, "error": None, "error_kind": None, "identity": None,
              "identity_verification": None, "http_status": None}
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(f"http://127.0.0.1:{PORT}/work", timeout=timeout) as response:
            result["http_status"] = response.status
            if response.status != 200:
                raise ValueError(f"Unexpected HTTP status {response.status}")
            payload = response.read(8193)
            if len(payload) > 8192:
                raise ValueError("Oversized service response")
            result["identity"] = _validate_identity(json.loads(payload))
            result["identity_verification"] = _verify_local_identity(result["identity"])
            if result["identity_verification"]["status"] == "ok":
                result["ok"] = True
            else:
                result.update(error=result["identity_verification"]["error"], error_kind="identity_unverified")
    except HTTPError as exc:
        description = f"HTTP {exc.code}" + (" (redirect rejected)" if 300 <= exc.code < 400 else "")
        result.update(error=description, error_kind="http_error", http_status=exc.code)
        exc.close()
    except (TimeoutError, socket.timeout) as exc:
        result.update(error=str(exc) or "Request timed out", error_kind="timeout")
    except URLError as exc:
        reason = exc.reason
        kind = "timeout" if isinstance(reason, (TimeoutError, socket.timeout)) else (
            "connection_refused" if isinstance(reason, ConnectionRefusedError) else "connection_error")
        result.update(error=str(reason), error_kind=kind)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result.update(error=str(exc), error_kind="invalid_response")
    result["completed_monotonic"] = time.monotonic()
    result["latency_ms"] = (result["completed_monotonic"] - started) * 1000
    return result


def snapshot():
    start = time.monotonic()
    unit = unit_state()
    data = unit.get("data") or {}
    try:
        pid = int(data.get("MainPID", "0"))
    except ValueError:
        pid = 0
    process = process_state(pid)
    cg = cgroup_state(data.get("ControlGroup"))
    sockets = socket_state()
    request = probe()
    # A valid looking unrelated listener must never count as functional recovery.
    observed = process.get("data") or {}
    identity = request.get("identity") or {}
    if request["ok"]:
        supervised = (unit.get("status") == "ok" and data.get("ActiveState") == "active"
                      and pid > 1 and process.get("status") == "ok")
        if not supervised:
            request.update(ok=False,
                           error="Cannot affirm that the response belongs to an active supervised main process: unit/process evidence unavailable or inactive",
                           error_kind="identity_unverified")
        elif identity.get("pid") != pid or any(identity.get(k) != observed.get(k)
                                               for k in ("pid", "start_ticks", "boot_id")):
            request.update(ok=False, error="Response process identity differs from observed main process (possible restart race)",
                           error_kind="identity_mismatch")
    journal = journal_state()
    return {"wall_time": wall_time(), "monotonic": start, "completed_monotonic": time.monotonic(),
            "unit": unit, "process": process, "cgroup": cg, "socket": sockets,
            "probe": request, "journal": journal}


def environment():
    result = {"platform": platform.platform(), "kernel": platform.release(),
              "python": platform.python_version(), "cgroup_v2": (CGROUP / "cgroup.controllers").exists()}
    try:
        result["systemd"] = command([SYSTEMCTL, "--version"]).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        result["systemd"] = None
        result["systemd_error"] = str(exc)
    return result
