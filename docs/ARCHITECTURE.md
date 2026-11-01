# Architecture and operating model

LSRP has one deliberately fallible service and a separate operator tool. The service performs deterministic SHA256 request work and exposes identity so that a successful response can be tied to a particular Linux process. The experiment harness controls fixed lab resources. The observer collects evidence; the report layer interprets it without treating the chosen fault label as a diagnosis.

## Service lifecycle

`lsrp-lab.service` executes `/usr/bin/python3 -u /opt/lsrp/lsrp/service.py` as `lsrp-lab`. systemd owns its lifecycle. `Restart=on-failure` and a restart delay expose the difference between a process exiting, a replacement process starting, and requests becoming available. Start-rate limits stop endless rapid restart attempts. Normal stopping uses SIGTERM and bounded cleanup. The installed unit files are the authority for exact policy; inspect them with:

```bash
systemctl cat lsrp-lab.service
systemctl show lsrp-lab.service --property=MainPID,ActiveState,SubState,Result,NRestarts,ControlGroup,InvocationID
journalctl -b -u lsrp-lab.service --no-pager -n 30
```

These semantics follow [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) and [systemd.unit](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html). Local systemd versions may support different directives; setup and validation expose incompatibility instead of simulating service management.

The service has three HTTP endpoints on `127.0.0.1:18765`:

| Endpoint | Meaning |
| --- | --- |
| `/live` | The request-handling service is alive enough to answer this check |
| `/ready` | The service considers itself ready to handle normal work |
| `/work` | Execute deterministic work and return a result plus process identity |

The identity includes service name, PID, process start ticks, boot ID, and an instance identifier. A listener that returns arbitrary HTTP 200 responses is insufficient. A process identity is the combination of boot ID, PID, and start time; PIDs alone can be reused.

## Fault control and constraints

The root operator uses an authenticated Unix-domain control socket to request a bounded service mode. `hang` keeps liveness available while normal work/readiness fail to complete. `cpu` creates synthetic CPU demand inside the service's cgroup. Its deadline expires using a monotonic clock. `normal` removes the mode. The synthetic workload is intentional and disclosed; the resulting process, socket, and cgroup observations are real.

The crash experiment uses the service manager and verified service identity. The collision experiment temporarily stops the main service, starts the fixed unprivileged collision helper on the same loopback port, and attempts main-service startup. The collision helper has an independent systemd runtime limit. The main service logs its real bind error.

CPU quota, memory ceiling, task count, and FD limit apply to the service. `CPUQuota=25%` corresponds to a quarter of one CPU's scheduling time, rather than 25% of every CPU. Memory and CPU values are service-local cgroup measurements. See [systemd resource control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html) for configuration and [cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html) for kernel interfaces.

## Observation and reporting

The read-only observer correlates seven observation families:

1. Unit state, exit result, restart count, timestamps, invocation ID, and cgroup path.
2. Process identity, parent PID, state, CPU time, resident memory, descriptors, and limits.
3. Cgroup CPU counters and quota, memory use/limit/events.
4. Fixed-port listener and ownership observations.
5. Identity-validated end-to-end work probes.
6. Recent current-boot journal entries scoped to the unit.
7. Wall-clock and monotonic observation times.

Each OS observation has a source, collection time, status, data, and error. Missing permissions, unsupported features, process disappearance during collection, and command failure remain explicit. Read-only commands may provide partial evidence when run unprivileged.

The harness saves a baseline, injection metadata, observed samples, recovery/cleanup events, and final interpretation. Diagnosis derives from observed samples. The experiment's known requested action is retained separately so that an operator can compare a blinded interpretation with harness ground truth. Recovery requires at least three consecutive identity-valid work responses; cleanup failure prevents a successful trial.

Snapshot collection is sequential and therefore not atomic. A process may exit between the systemd read, `/proc` read, and probe. The report must preserve that uncertainty. An observer also consumes resources, so the overhead experiment records a separate workload comparison.

## Repository map

| Path | Responsibility |
| --- | --- |
| `lsrp/service.py` | Request service, process identity, bounded modes, shutdown |
| `lsrp/collision.py` | Fixed-port collision helper |
| `lsrp/observe.py` | Read-only Linux and application evidence |
| `lsrp/lab.py` | Installation checks, fixed-resource operations, locking |
| `lsrp/runner.py` | Incident lifecycle, cleanup, trials, overhead experiment |
| `lsrp/report.py`, `lsrp/stats.py` | Evidence interpretation and repeated-trial statistics |
| `lsrp/cli.py` | Operator interface and exit semantics |
| `deploy/` | systemd unit definitions |
| `scripts/` | Installation, teardown, real Linux validation |
| `tests/` | Portable and Linux-specific automated checks |

## Deliberate limits

This release supports a single service and fixed loopback address. It neither monitors arbitrary services nor changes external routing or firewall policy. There is no distributed availability claim, production remediation engine, automatic root-cause guarantee, or dashboard. Container execution without real systemd/cgroup governance cannot stand in for the required VM validation.
