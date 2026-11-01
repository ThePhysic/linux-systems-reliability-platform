# Intended System and Architecture

## Conceptual components

### A. Deliberately fallible lab service (`labsvc`)

A minimal real network service with useful liveness/readiness semantics. It should have a stable normal workload, support bounded synthetic stress/latency modes for experiments, handle normal termination, and expose enough identity to correlate it with the Linux process and listening socket. Language is flexible; the core learning goal is Linux operational behavior, not framework complexity.

### B. Service supervision

A real **systemd service unit** controls the lab service, including startup, stopping, restart policy and limits. Its service state, journal, restarts, main PID, exit codes and resource restrictions are observable. The service's normal identity and lifecycle should be understood, not hidden behind a Docker orchestration abstraction. Where isolation requires a VM or namespace, use it as the enclosing lab boundary rather than pretending it substitutes for systemd.

### C. Linux observation layer

A predominantly read-only collector that correlates:

- Service manager state: unit status, result, restarts, timestamps, last exit.
- Process state: PID plus start time for reuse defense, parent/children, state, CPU time, RSS and file-descriptor counts.
- Resource governance: applicable cgroup path, CPU and memory counters/limits and OOM evidence where available.
- Network state: intended listening address and port, socket presence, connection attempts and failures.
- Application checks: successful requests, timeouts, response latency, readiness results.
- Journal/log context: recent relevant entries with timestamps and provenance.

Unavailable or permission-restricted data should be represented explicitly, never invented or quietly replaced with zero.

### D. Incident evidence and reasoning

An incident representation connecting **what was observed**, **when**, **from which OS interface or command**, **for which process/unit**, and **what that observation can or cannot establish**. Examples: a failed HTTP request is evidence of unavailability, but not proof of a crash; a PID disappearing and systemd recording a signal exit offers a more specific explanation.

A useful summary separates **symptom**, **evidence**, **hypotheses**, **confirmed mechanism when known**, **recovery actions**, and **verification**. Avoid automatic causal claims beyond the observations.

### E. Fault experiment harness

A bounded, explicitly lab-targeted mechanism for inducing controlled faults. It should identify the intended service/unit/PID/cgroup/network namespace, confirm ownership, and avoid affecting unrelated host workloads. Faults may include exiting the lab process, making it unresponsive, stressing its allowed CPU budget, or creating a conflict for its test port. Design for completion, restoration, and clear failure reporting.

### F. Operator interface

CLI-first commands or equivalent simple operator controls for checking health, viewing recent evidence, exercising a known incident and inspecting recovery results. The interface is intended to make Linux evidence accessible, not to abstract away what Linux is doing. Output should be machine-readable where it improves repeatability.

## Conceptual data flow

```text
Controlled Linux environment
        |
        +-- systemd --> labsvc (real PID, sockets, workloads)
        |                  |
        |                  +--> request/health signals
        |
        +-- /proc, cgroups, journald, socket state
        |                  |
        +------------ observation layer
                           |
    bounded fault --> incident timeline / evidence report
                           |
                     recovery verification
                           |
                  repeatable trial records
```

## Data semantics that matter

- Use monotonic elapsed time within a trial where possible; preserve wall-clock timestamps for human correlation.
- Track process identity beyond PID alone so restarts/PID reuse do not corrupt the timeline.
- Record collection timestamps, units and data source of each measurement.
- Report errors, missing permissions and unsupported kernel features honestly.
- Preserve provenance without capturing host secrets or unrelated process details.
- Distinguish service manager restart success from **end-to-end functional recovery**.

## Deployment model

The intended lab is Linux-native and safely isolated. A Linux VM is a natural default, particularly for a Windows development host. A host-level or production-adjacent installation is not required. The project should explain relevant environmental dependencies (distribution, kernel, cgroup v2 availability, systemd, namespace permissions) and degrade transparently where unsupported.
