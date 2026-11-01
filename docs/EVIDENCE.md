# Evidence, record format, and measurements

Saved JSON is intended for independent inspection. A summary is useful only when a reviewer can connect it to the recorded observations. All measurements are specific to their lab, parameters, sampling policy, and software revision.

## Trial record

Every trial has `schema_version: 1` and a UUID `trial_id`. The main fields are:

| Field | Meaning |
| --- | --- |
| `scenario` | Requested experiment: `crash`, `hang`, `cpu`, or `collision`; experiment ground truth, not diagnosis |
| `environment` | Kernel/platform/service-manager and lab metadata available to the runner |
| `parameters` | Requested fault duration and other actual sampling/recovery settings |
| `baseline` | Ordered pre-fault snapshots |
| `injection` | Requested action, timestamp, confirmation, and details known to the harness |
| `samples` | Ordered observed snapshots during the incident and recovery |
| `events` | Timestamped runner actions and lifecycle notes |
| `cleanup` | Cleanup outcome, errors, and independent verification |
| `outcome` | `recovered`, `failed`, or `inconclusive` |
| `record_status` | `running` for an incomplete checkpoint; `complete` after final analysis and cleanup attempt |
| `metrics` | Derived measurements with units and unavailable values preserved |
| `limitations` | Sampling limits, missing sources, and interpretation uncertainty |

The report command regenerates interpretation from these observations. It must not infer a crash merely because `scenario` is `crash`, or conclude throttling because the requested mode was `cpu`. Keep injection details visible as ground truth separately from telemetry-derived findings.

## Snapshot and evidence envelopes

Each snapshot contains `wall_time` (UTC ISO timestamp), `monotonic` (seconds in the current boot's monotonic clock), and `unit`, `process`, `cgroup`, `socket`, `probe`, and `journal` observations. An OS evidence envelope has this shape:

```json
{
  "source": "description of the exact command or kernel interface",
  "collected_at": "UTC ISO timestamp",
  "status": "ok",
  "data": {},
  "error": null
}
```

`status` can be `ok`, `unavailable`, or `error`. Unavailable data uses `data: null` and an explanation; an unavailable count is never silently replaced with zero. Some collections contain per-file/per-measurement details so partial availability remains inspectable.

Probes record at least success, latency in milliseconds, error, identity, request source, and timestamps. A valid `/work` response must contain the expected service identity and deterministic result. Connection refusal, timeout, non-success HTTP response, malformed response, wrong identity, and wrong result are distinct observations.

The evidence is scoped to the fixed unit, its process/cgroup, recent unit journal, and the lab port. Journal entries use an allowlist and bounded recent history; arbitrary process environments and host-wide logs are not exported. Review your own artifacts before sharing them: kernel versions, machine identifiers, PIDs, and operational timing may still be sensitive in some environments.

## Identity and races

Process identity combines boot ID, PID, and `/proc/<pid>/stat` start ticks. CPU time is cumulative seconds, RSS is resident bytes, FD count is a count, and cgroup CPU counters retain their kernel units. A replacement process resets process-local counters; never compute a CPU delta across different process identities. A recreated cgroup can reset counters too. Counter regressions are unavailable deltas, not negative pressure or zero usage.

Collection is not an atomic kernel snapshot. The process can exit between reading systemd properties and `/proc`, or between `/proc` and HTTP. Explicit race observations are preferable to attributing one process's resource data to another. The kernel's [process interface documentation](https://docs.kernel.org/filesystems/proc.html) explains the interface and access limits.

## Time and recovery

Monotonic time measures elapsed intervals inside a trial. UTC wall time enables human correlation with journald. Do not subtract unrelated machines' monotonic timestamps or interpret wall-clock jumps as incident duration.

Failure onset is not known at infinite resolution. The harness records when it requested and confirmed the injection; observations bound when a symptom appeared. Detection latency depends on polling interval, probe timeout, sequential collector duration, and any delay in the requested operation. Reports distinguish observation-based timing from injection-relative timing. If no failure was sampled, unavailability duration remains unknown.

Recovery requires **at least three consecutive identity-valid work requests** after the incident. The first success in the final sustained sequence is the observed recovery point; completion of that sequence establishes sustained recovery. A later failure, untimely response, fault mode, or process-identity change invalidates an earlier provisional recovery; the complete observation window is analyzed. Provisional segments and relapses remain visible. The unit becoming active or acquiring a new PID is a separate lifecycle event. Resource-fault recovery additionally requires the fault condition to be removed and cleanup verification to pass.

## Repeated-trial statistics

Comparison groups trials by scenario and retains the number of recovered, failed, and inconclusive runs. For each numeric measurement, retain the available sample count along with median and range. Missing measurements stay missing. A p95 is reported only at 20 or more observations; even then its usefulness depends on representativeness and adequate sample count.

Three repeated trials give a reproducibility check and a small-sample spread. They do not establish production SLOs, reliability probabilities, or a stable p95. Do not combine different kernels, restart policies, quotas, polling intervals, probe work, or VM resource settings into a single claim without disclosing the difference.

## Observer overhead

The overhead command compares a representative request workload with and without full snapshot collection, repeats the comparison, and retains raw trial measurements. Report observer frequency and run duration, request success and latency/throughput, and the lab environment. Measurement noise or a faster observed sample does not prove monitoring has negative overhead. Short VM trials are noisy and can be affected by host scheduling, page cache, Python startup, or other workloads.

CPU quota accounting is governed by the service cgroup, including service-created stress workers. Main-process CPU time can differ from cgroup CPU time. Quota throttling requires relevant kernel counter evidence, not merely a latency spike. See the authoritative [cgroup v2 interface](https://docs.kernel.org/admin-guide/cgroup-v2.html).

## Outcome interpretation

- **Recovered:** Required incident observations and functional restoration were established, with successful cleanup.
- **Failed:** An operation, validation, or restoration failed; use the preserved error/evidence to investigate.
- **Inconclusive:** Available observations cannot establish the required incident or recovery claim.

The CLI returns nonzero for failed/inconclusive trials. Record artifacts are preserved so that automation and an operator can examine the same evidence. Do not discard unsuccessful trials from a portfolio result.

The runner atomically checkpoints before fault dispatch and after each observation. SIGINT/SIGTERM attempts cleanup and saves a completed failed record. SIGKILL cannot execute cleanup: its checkpoint remains `running`, `inconclusive`, with cleanup unverified. The independent timer can restore the service, but does not rewrite the killed runner's claim. Run the interruption validator to observe this distinction directly.
