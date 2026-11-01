# Quality Bar and Success Criteria

## Core functional expectations

- A real Linux service performs meaningful request/response work and supports clear liveness/readiness checks.
- Service lifecycle is actually managed by systemd, with inspectable policy and unit state.
- A Linux observation layer correlates process, service, resource, journal and socket evidence.
- Controlled failure scenarios have reproducible symptoms and verified recovery or an explicit failed-recovery outcome.
- Incident output connects timestamps, sources, observations, explanations and limitations.
- The operator can repeat a scenario without stale process identities or contaminated state causing misleading results.

## Diagnostic correctness

**The following distinctions must remain visible:**

| Observable event | What it does not prove alone |
| --- | --- |
| HTTP timeout | That the process crashed |
| systemd unit active | That requests are succeeding |
| CPU utilization rises | That throttling is occurring |
| Process PID changes | That the service has fully recovered |
| Bind error | That the underlying network is broken |
| Memory RSS increases | That an OOM event occurred |

Each interpretation should point to the evidence required to support it. Inconclusive scenarios are valid outcomes.

## Quantitative measurements

Where appropriate, measure:

- **Failure-to-detection latency:** observable failure onset to first correctly recognized symptom.
- **Failure-to-recovery time:** observable failure onset to first sustained return of successful service checks.
- **Systemd restart delay:** termination/start event gap, separately from application readiness.
- **Probe success ratio and request latency:** before, during and after an incident.
- **Resource pressure:** service-local CPU/memory/FD metrics with units and scope.
- **Observation overhead:** impact of monitoring frequency on a representative workload.

Repeated trials should produce a distribution (at minimum sample count and median; preferably p95/range where sample size justifies it), not a single headline number. The actual measured values remain unknown until implementation and experimentation; there are **no precommitted performance claims**.

## Safety and boundary criteria

- All faults affect controlled, identifiable lab-owned processes, ports, cgroups or namespaces only.
- Tests run in an isolated Linux environment for potentially disruptive resource or networking experiments.
- Ordinary service functionality does not require unrestricted root execution.
- Faults and stressors have enforced limits, safe termination behavior and independently verifiable cleanup.
- Failed preconditions or ambiguous ownership should prevent a disruptive operation.
- No host-wide process kills, uncontrolled OOM loads, changes to external networks or persistent host firewall/routing changes.

## Testability and robustness

- Normal lifecycle, failure behavior, telemetry parsing, incident chronology and recovery semantics are testable.
- Fault runs produce repeatable machine-readable records plus understandable explanations.
- Edge cases include missing OS data, permission errors, unexpectedly exited services, clock/sampling uncertainty, stale PID reuse and interrupted cleanup.
- The lab can be returned to a known clean condition; restore failures are surfaced.
- Documentation accurately identifies platform assumptions and limitations.

## What success looks like to a skeptical reviewer

A reviewer can inspect an incident, cross-check key claims using the Linux tools and underlying data, understand why the diagnosis is justified, see a repeat run, and verify that the experiment did not modify unrelated system resources. This bar matters more than visual polish or a high feature count.
