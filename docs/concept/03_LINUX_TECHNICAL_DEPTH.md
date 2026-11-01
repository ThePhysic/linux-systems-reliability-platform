# Technical Knowledge This Project Should Demonstrate

The goal is demonstrable understanding, not tool-name accumulation.

## Process lifecycle and signals

- Start, stop, terminate and restart a supervised process; observe exit status and signals.
- Handle SIGTERM gracefully and contrast it with unexpected termination.
- Distinguish a stopped process, a running-but-unresponsive process and a service with no listening socket.
- Understand parent/child relationships and protect process identity against PID reuse.

## `/proc` and resource observability

- Interpret selected `/proc/<pid>` and `/proc` data correctly, including units and sampling limitations.
- Explain CPU time vs CPU utilization, resident memory vs virtual size, and number of open FDs vs limits.
- Use suitable Linux tools (e.g. `ps`, `top`, `ss`, `lsof`, `strace`, `journalctl`) for investigation, recognizing availability and permission constraints.
- Make sampling effects and race conditions explicit when a process exits between reads.

## systemd, journald and operating a service

- Show what the unit configuration means for dependencies, restart conditions, restart limits, kill behavior and resource sandboxing.
- Understand service-active state vs application-ready state.
- Correlate journal records, invocation identity, restart events and actual request availability.
- Avoid designs where restart loops are silently interpreted as healthy service operation.

## cgroups and resource constraints

- Understand where CPU/memory limits apply and how a service is associated with a cgroup.
- Identify resource pressure, throttling and out-of-memory behavior using Linux-provided evidence.
- Avoid inferring OOM or throttling solely from a slow request.
- Account for differences between host-global statistics and service-local accounting.

## TCP/IP and socket behavior

- Explain bind/listen/accept and what a port collision means for startup.
- Distinguish connection refusal, timeout, application timeout, and loss of readiness.
- Understand address/port ownership and how `ss` or equivalent tools corroborate a service's socket state.
- If network namespaces are used, explain routing, loopback, reachability and cleanup in the isolated lab.

## Diagnostics and experimental reasoning

- Use direct evidence to test competing hypotheses: crashed vs hung, overloaded vs disconnected, bind failure vs failed health check.
- Demonstrate how the diagnostic approach changes when only some observations are available.
- Explain the observer effect: frequent polling or tracing can affect results, especially in small lab workloads.
- Separate cause of injected fault (known to experiment harness) from what an operator could independently infer from telemetry.

## Reliability, cleanup and security

- Prefer non-root service execution and constrained privileges.
- Bound expensive operations and isolate intentionally harmful conditions from the developer's machine.
- Restore lab state after interruption; leave no long-running CPU burners, port squatters or altered host network configuration.
- Make test failures and incomplete cleanup visible rather than assuming success.
