# LSRP implementation contract

Python 3.11+ standard library, package `lsrp` at repository root. CLI `python3 -m lsrp` (installed console script `lsrp`). This contract covers one Linux/systemd service and a CLI; graphical dashboards are outside the current scope. All disruptive operations require root, a root-owned `/etc/lsrp/lab.json` marker, systemd PID 1 and cgroup v2. Only fixed units and loopback ports may be controlled.

## Fixed lab layout

- Main unit `lsrp-lab.service`, collision helper `lsrp-collision.service`.
- Main service account `lsrp-lab`, executable `/opt/lsrp/lsrp/service.py` using `/usr/bin/python3 -u`.
- Bind `127.0.0.1:18765`. GET `/live`, `/ready`, `/work` return JSON with `service: "lsrp-lab"`, `pid`, `start_ticks`, `boot_id`, `instance`, `mode`. `/work` adds deterministic SHA256 result for fixed input; `/live` and normal `/ready` status 200.
- Fixed authenticated Unix control socket `/run/lsrp-lab/control.sock`; control requests are newline JSON `{mode: "normal"|"hang"|"cpu", duration: number}`. Peer must be root (SO_PEERCRED). Hang affects ready/work only; CPU stress is a bounded worker within the main service cgroup; restoration automatic by monotonic TTL <=15 s even if runner dies. CLI may request normal.
- Main unit CPUQuota=25%, MemoryMax=128M, TasksMax=32, LimitNOFILE=256; Restart=on-failure, RestartSec=2s, StartLimitBurst=5, StartLimitIntervalSec=30s, TimeoutStopSec=5s. No privilege in service. Collision unit bounded RuntimeMaxSec=15s and same unprivileged UID; binds fixed port and never returns valid service identity. Main service must log bind errors.
- Marker JSON schema_version 1, machine_id, service_uid, port 18765, unit `lsrp-lab.service`, install_id UUID. Setup writes marker after installing; CLI confirms identity, permissions, installation and boundaries before mutations. Root owns code, units, marker; service cannot change these. Refuse symlink config.

## Collector API

`observe.snapshot() -> dict`: `wall_time` UTC ISO, `monotonic` float, `unit` evidence, `process` evidence, `cgroup` evidence, `socket` evidence, `probe` dict, `journal` evidence. Evidence envelope `{source: str, collected_at: ISO, status: "ok"|"unavailable"|"error", data: object|null, error: str|null}`. Unit data systemctl key/value including MainPID, ActiveState, SubState, Result, NRestarts, ExecMainCode/Status, ExecMainStartTimestampMonotonic, ControlGroup, InvocationID. Process data PID/start_ticks/boot_id/state/ppid/cpu_seconds/rss_bytes/fd_count and limits. Cgroup data applicable path, cpu.stat/cpu.max/memory.current/memory.max/memory.events. Socket include ss listener ownership. Probe checks `/work` via no proxy, timeout ~0.4s, validates JSON identity and work result, `ok`, `latency_ms`, `error`, `identity`, `source`, timestamps; `observe.probe(timeout=0.4)` available standalone. Journal limited to unit and current boot, fields allowlist, <=20 recent entries. Missing observations explicit, sampling races never zero substituted. Observation read only.

## Record/report API

Trial dict schema_version 1, trial_id UUID, scenario `crash|hang|cpu|collision`, environment, parameters, baseline list snapshots, samples list snapshots including recovery, injection `{requested_at, monotonic, action, confirmed, details}`, events list `{wall_time, monotonic, phase, message}`, cleanup `{ok, errors, verification}`, outcome `recovered|failed|inconclusive`, metrics, limitations. Recovery must be >=3 consecutive valid work responses. `report.analyze(trial) -> dict` returns evidence-only diagnosis, metrics (detection/recovery onset with uncertainty, CPU deltas/latency); must not use scenario/injection to infer root cause. `report.render(trial) -> str` readable text; `stats.summarize(trials) -> dict` group by scenario, n, recovered/failed/inconclusive, medians/ranges, p95 only >=20, preserve unknowns.

## Intended CLI

`doctor [--json]`, `status [--json]`, `start`, `stop`, `run {crash,hang,cpu,collision} --duration 4 --output PATH`, `suite --trials 3 --duration 4 --output DIR`, `report FILE [--json]`, `compare FILE_OR_DIR... [--json]`, `overhead --duration 4 --trials 3 --output PATH`, `cleanup`. Mutations locked with flock, run bounded and signals cleanup, no arbitrary unit/PID/host arguments. Normal/read-only commands unprivileged when OS permissions permit. CLI faults return nonzero for failed/inconclusive outcomes. Trial files written even when interrupted. Suite reports real aggregate. Real Linux validation is mandatory before completion.
