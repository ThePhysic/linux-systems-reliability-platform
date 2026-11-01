# Diagnostic walkthrough

Begin with baseline operation, keep the operator's evidence separate from the requested experiment, and finish with successful end-to-end requests. The following commands run inside an installed disposable Linux lab. Record actual outputs; the examples here are commands, not fabricated results.

## Baseline

```bash
sudo python3 -m lsrp start
python3 -m lsrp status --json
curl --fail --max-time 1 http://127.0.0.1:18765/live
curl --fail --max-time 1 http://127.0.0.1:18765/ready
curl --fail --max-time 1 http://127.0.0.1:18765/work
ss -ltnp 'sport = :18765'
```

Match the work-response identity with the observer's process identity and systemd MainPID. Record ordinary request latency before interpreting a later slow response. If journal/FD ownership information is unavailable to your user, inspect a root-collected snapshot and retain the original permission error.

## Unexpected exit

```bash
sudo python3 -m lsrp run crash --duration 4 --output artifacts/crash.json
python3 -m lsrp report artifacts/crash.json
```

Look for a disappearing old identity, exit/signal evidence, systemd restart state or journal entries, and a new identity. Probe failure establishes request unavailability; it does not by itself establish a crash. A changed PID establishes a lifecycle change only when paired with start-time/boot identity. Check the service-manager restart gap separately from the return of successful requests. Recovery is sustained successful work, not a log message announcing startup.

## Live but unresponsive

```bash
sudo python3 -m lsrp run hang --duration 4 --output artifacts/hang.json
python3 -m lsrp report artifacts/hang.json
```

Compare failing work requests with a still-present process, an active unit, and the listener. During the fault, `/live` can succeed while `/ready` and `/work` fail to complete within the probe timeout. This is an intentionally injected application hang mode; the independent evidence supports a live-but-unresponsive symptom, not an arbitrary deadlock's exact internal cause. Once the mode expires or is removed, verify that requests resume with appropriate identity and timing.

## CPU pressure and throttling

```bash
sudo python3 -m lsrp run cpu --duration 4 --output artifacts/cpu.json
python3 -m lsrp report artifacts/cpu.json
```

Use deltas across samples in the same cgroup lifetime. Increasing `usage_usec` establishes consumed CPU time. Increasing `nr_throttled` or `throttled_usec`, with a finite `cpu.max`, corroborates quota throttling. A slow request or high CPU utilization alone cannot establish throttling. Compare baseline, incident, and recovery latencies and resource samples. If the environment provides no reliable throttling evidence, record an inconclusive mechanism rather than inventing counters.

The load is bounded synthetic work in the service cgroup. A small experiment may produce clear throttling without a large latency regression. Neither a latency improvement nor a fixed amount of degradation is promised.

## Bind conflict

```bash
sudo python3 -m lsrp run collision --duration 4 --output artifacts/collision.json
python3 -m lsrp report artifacts/collision.json
```

Look for the startup bind error, the collision helper's listener, the missing intended service listener, and failed/activating unit state. A reachable port or HTTP response from the helper is not healthy service work; response identity and deterministic result must match the intended service. A bind error identifies an address/port allocation problem, not a general network outage. Confirm the conflicting helper is gone, then verify main-service requests.

## Compare trials

```bash
sudo python3 -m lsrp suite --trials 3 --duration 4 --output artifacts/trials
python3 -m lsrp compare artifacts/trials --json
```

Use counts, medians, and ranges for small samples. The CLI reports p95 only with sufficient samples; three trials do not support a stable tail-latency claim. Compare matching environment and parameters. Keep failed and inconclusive trials in the denominator, and report missing metric counts. An absent outage sample can mean the interruption fell between polls; it is not a zero-length outage.

## A useful false lead

Start with “systemd says active, therefore the service is healthy.” The hang experiment demonstrates why this inference fails: process and service-manager liveness can coexist with failing work. Use those observations to narrow competing hypotheses, then show recovery through requests. This reasoning is the project's central demonstration.

## Cross-checking sources

- [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html): startup types, exit and restart behavior.
- [journalctl](https://www.freedesktop.org/software/systemd/man/latest/journalctl.html): unit and boot filtering; retained logs are bounded and may be incomplete.
- [Linux `/proc` documentation](https://docs.kernel.org/filesystems/proc.html): process state and resource interfaces, access limits, and races.
- [Linux cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html): CPU and memory counter semantics.

The exact injected cause is known to the experiment harness. The report's evidence-based mechanism must still stand on its recorded observations. A legitimate result may remain uncertain.
