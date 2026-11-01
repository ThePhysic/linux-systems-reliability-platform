"""Fixed lab boundary, ownership checks, and bounded systemd operations."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import stat
import struct
import subprocess
import time

UNIT = "lsrp-lab.service"
COLLISION = "lsrp-collision.service"
RECOVER = "lsrp-recover.service"
TIMER = "lsrp-recover.timer"
PORT = 18765
MARKER = Path("/etc/lsrp/lab.json")
CONTROL = "/run/lsrp-lab/control.sock"


class LabError(RuntimeError):
    """A precondition or controlled operation failed."""


def command(args: list[str], timeout: float = 10, check: bool = True) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LabError(f"{args[0]}: {exc}") from exc
    if check and result.returncode:
        raise LabError(f"{' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def systemctl(*args: str, check: bool = True) -> str:
    return command(["/usr/bin/systemctl", *args], check=check)


def properties(unit: str = UNIT) -> dict[str, str]:
    keys = "LoadState,FragmentPath,DropInPaths,User,MainPID,ActiveState,SubState,ControlGroup,ExecStart,CPUQuotaPerSecUSec,MemoryMax,TasksMax,LimitNOFILE"
    return dict(line.split("=", 1) for line in systemctl("show", unit, f"--property={keys}").splitlines() if "=" in line)


def _trusted(path: Path, directory: bool = False) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise LabError(f"Untrusted ownership/permissions: {path}")
    if directory and not stat.S_ISDIR(info.st_mode):
        raise LabError(f"Expected directory: {path}")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise LabError(f"Expected regular file: {path}")


def guard(require_root: bool = True) -> dict:
    if platform.system() != "Linux":
        raise LabError("Use an isolated Linux lab with systemd; this command cannot run on Windows.")
    if require_root and os.geteuid() != 0:
        raise LabError("Lab mutation requires root inside the disposable Linux lab (use sudo).")
    try:
        if Path("/proc/1/comm").read_text().strip() != "systemd":
            raise LabError("PID 1 must be systemd.")
        if not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
            raise LabError("A cgroup v2 hierarchy is required.")
        for p in [Path("/etc/lsrp"), Path("/opt/lsrp"), Path("/opt/lsrp/lsrp")]:
            _trusted(p, directory=True)
        _trusted(MARKER)
        data = json.loads(MARKER.read_text())
        if (data.get("schema_version") != 1 or data.get("unit") != UNIT
                or data.get("port") != PORT or not data.get("install_id")
                or data.get("machine_id") != Path("/etc/machine-id").read_text().strip()):
            raise LabError("Lab marker does not match this installation/machine.")
        import pwd
        uid = pwd.getpwnam("lsrp-lab").pw_uid
        if uid == 0 or data.get("service_uid") != uid:
            raise LabError("Lab service must have its dedicated non-root UID.")
        for unit in [UNIT, COLLISION, RECOVER, TIMER]:
            expected = Path("/etc/systemd/system") / unit
            _trusted(expected)
            props = properties(unit)
            if props.get("FragmentPath") != str(expected) or props.get("DropInPaths"):
                raise LabError(f"Unexpected unit fragment/drop-ins for {unit}.")
            if unit in [UNIT, COLLISION] and props.get("User") != "lsrp-lab":
                raise LabError(f"Unexpected service identity for {unit}.")
        # Root-owned manifest binds the installation to exactly the reviewed code/units.
        manifest = data.get("sha256", {})
        required = {"/opt/lsrp/lsrp/service.py", "/opt/lsrp/lsrp/collision.py"}
        required.update(f"/etc/systemd/system/{u}" for u in [UNIT, COLLISION, RECOVER, TIMER])
        if not isinstance(manifest, dict) or not required.issubset(manifest):
            raise LabError("Installation manifest missing; rerun the lab installer.")
        for name, digest in manifest.items():
            path = Path(name)
            if not (name.startswith("/opt/lsrp/") or name in required):
                raise LabError("Unexpected manifest path.")
            _trusted(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise LabError(f"Installed file changed: {path}; reinstall before running faults.")
        return data
    except (OSError, ValueError, KeyError) as exc:
        raise LabError(f"Lab unavailable: {exc}. Run scripts/install-lab.sh in a disposable Linux lab.") from exc


@contextlib.contextmanager
def locked():
    import fcntl
    guard()
    _trusted(Path("/run/lsrp"), directory=True)
    fd = os.open("/run/lsrp/lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise LabError("Untrusted experiment lock.")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LabError("Another LSRP operation is running.") from exc
        yield
    finally:
        os.close(fd)


def identity() -> dict:
    props = properties()
    pid = int(props.get("MainPID", "0"))
    if pid <= 1:
        raise LabError("Lab service has no main process.")
    path = Path(f"/proc/{pid}")
    st = (path / "stat").read_text()
    fields = st[st.rfind(")") + 2:].split()
    uid = (path / "status").read_text().split("Uid:", 1)[1].split()[0]
    import pwd
    if int(uid) != pwd.getpwnam("lsrp-lab").pw_uid:
        raise LabError("Main process UID does not belong to the lab.")
    if props.get("ControlGroup") not in (path / "cgroup").read_text():
        raise LabError("Main process cgroup does not match the lab unit.")
    return {"pid": pid, "start_ticks": int(fields[19]),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def control(mode: str, duration: float = 0) -> dict:
    owner = identity()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(CONTROL)
        peer_pid, peer_uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        import pwd
        if peer_pid != owner["pid"] or peer_uid != pwd.getpwnam("lsrp-lab").pw_uid:
            raise LabError("Control socket peer does not match the supervised service.")
        sock.sendall(json.dumps({"mode": mode, "duration": duration}).encode() + b"\n")
        response = b""
        while b"\n" not in response and len(response) < 4096:
            chunk = sock.recv(4096 - len(response))
            if not chunk:
                break
            response += chunk
        result = json.loads(response)
        if not result.get("ok"):
            raise LabError(f"Control rejected: {result}")
        return result


def start() -> None:
    # Inactive units may already have been garbage collected by systemd.
    # reset-failed then reports 'not loaded'; the checked start reloads the unit.
    systemctl("reset-failed", UNIT, check=False)
    systemctl("start", UNIT)


def cleanup(restart: bool = True) -> dict:
    errors = []
    for args in [("stop", TIMER), ("stop", RECOVER), ("stop", COLLISION)]:
        try:
            systemctl(*args)
        except LabError as exc:
            errors.append(str(exc))
    try:
        systemctl("reset-failed", COLLISION, RECOVER, check=False)
    except LabError as exc:
        errors.append(str(exc))
    try:
        systemctl("stop", UNIT)
        if restart:
            start()
    except LabError as exc:
        errors.append(str(exc))
    verification = {}
    for unit in [TIMER, RECOVER, COLLISION]:
        try:
            state = properties(unit).get("ActiveState")
            verification[unit] = state
            if state not in ["inactive", "failed"]:
                errors.append(f"{unit} still {state}")
        except LabError as exc:
            errors.append(str(exc))
    return {"ok": not errors, "errors": errors, "verification": verification}


def environment() -> dict:
    result = {"kernel": platform.release(), "architecture": platform.machine(), "python": platform.python_version(),
              "clock": "time.monotonic (seconds); UTC wall clock for correlation"}
    for key, args in [("systemd", ["/usr/bin/systemctl", "--version"]),
                      ("virtualization", ["/usr/bin/systemd-detect-virt"])]:
        try:
            result[key] = command(args, check=False).strip().splitlines()[0]
        except (LabError, IndexError) as exc:
            result[key] = f"unavailable: {exc}"
    try:
        result["os_release"] = Path("/etc/os-release").read_text()
    except OSError:
        result["os_release"] = None
    return result
