# Incident Scenarios and Diagnostic Stories

These are representative **behaviors to support**, not an implementation schedule. Favor fully understood scenarios over numerous shallow demonstrations.

## Core incident A — Unexpected process exit

**Symptom:** Requests fail when `labsvc` disappears, then may recover when systemd restarts it.  
**Linux evidence:** old/new process identity, exit code or signal, unit restart counter, journal, gap in successful requests.  
**Lesson:** process failure, supervision policy, restart loops and functional recovery are different states.  
**Recovery proof:** observe fresh PID/identity and successful requests after restart.

## Core incident B — Process lives but requests hang

**Symptom:** Unit and PID appear active, yet probes time out or readiness fails.  
**Linux evidence:** process still present, socket state, request timeout timestamps, potentially thread/process state and CPU counters.  
**Lesson:** liveness is not readiness; 'active' is not healthy.  
**Recovery proof:** service resumes successful timely responses after the fault is removed or the service is properly recovered.

## Core incident C — CPU pressure / throttling

**Symptom:** Request latency degrades and timeouts may increase while workload runs.  
**Linux evidence:** changes in CPU time/utilization, cgroup CPU statistics and throttling (if configured), response-latency distribution.  
**Lesson:** differentiate workload pressure, resource budget limits, and other causes of slowness.  
**Recovery proof:** controlled load ends, resource statistics stabilize, service behavior returns to baseline.

## Core incident D — Bind / port collision

**Symptom:** Service cannot start or accept traffic on its intended port.  
**Linux evidence:** startup error and journald record, active listener/port owner, absent intended listener, unit failure.  
**Lesson:** a startup/network resource failure is not the same as a running application bug.  
**Recovery proof:** lab-owned collision removed and the service can bind and pass checks.

## Useful deeper scenarios

**Memory pressure or OOM:** study cgroup memory accounting, memory events, RSS and service exit. Ensure pressure is inside an isolated, capped lab; never induce unbounded host memory exhaustion.

**File-descriptor exhaustion:** connect open-file limits, observed FD growth and failed accept/open calls. Clean up all owned descriptors and sockets.

**Network namespace disruption:** isolate a networking fault to a throwaway namespace; interpret connection refused, route failure, dropped traffic and application errors distinctly.

**Repeated crash / backoff / restart-rate limits:** inspect service-manager policy and why repeated auto-restarts eventually stop or are delayed.

**Graceful shutdown vs forced termination:** compare in-flight requests and resource cleanup during controlled termination and abrupt exit.

## Incident record example (conceptual)

```text
Incident: labsvc unavailable after unexpected exit
Environment: Linux test VM, specific kernel/systemd version
Observation: probe failures from T1 to T2
Evidence: journal reports exit by signal; old process identity gone
Evidence: systemd restart; new process identity observed at T3
Verification: successful probes resume at T4
Interpretation: labsvc exited and was restarted; exact exit mechanism
                corroborated by injected-fault record
Metrics: detection = T_detect - T_failure; recovery = T4 - T_failure
Limitations: sampling resolution; restart policy effects
```

Unknown or inconclusive evidence should be surfaced as such. The report must never quietly rely on knowing the fault injection label while presenting the result as an independent diagnosis.
