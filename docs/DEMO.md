# A real incident: the process was alive, but work timed out

This walkthrough is a **selective summary of an actual saved Linux trial**, not a simulated terminal session. It demonstrates the difference between systemd/process liveness and functional service health.

## Inspect it offline

From the repository root, with Python 3.11 or newer, run these read-only commands. They require neither root nor a running Linux lab and do not inject a fault or change the saved records.

Linux:

```bash
python3 -m lsrp report examples/robustness/validation/trials/hang-325476e5-045c-4fa9-96ef-4d7d4c803f65.json
python3 -m lsrp compare examples/robustness/validation/trials --json
```

Windows PowerShell:

```powershell
python -m lsrp report examples/robustness/validation/trials/hang-325476e5-045c-4fa9-96ef-4d7d4c803f65.json
python -m lsrp compare examples/robustness/validation/trials --json
```

The report is regenerated from the observations by the checked-out code; its presentation may change. The summary below refers directly to the immutable JSON. Creating new incident records requires the [disposable Linux environment](../README.md#requirements).

## Source and provenance

- **Exact record:** [`examples/robustness/validation/trials/hang-325476e5-045c-4fa9-96ef-4d7d4c803f65.json`](../examples/robustness/validation/trials/hang-325476e5-045c-4fa9-96ef-4d7d4c803f65.json).
- **Scenario and recorded trial ID:** `hang`; `d33f894b-58ab-4c3b-aa8f-7d19dec0df94`. The filename and embedded trial ID are distinct identifiers; both are preserved from the original run.
- **Validated source:** [`e49d3595eaa23410223790f5cebf3c4643f10a9e`](https://github.com/ThePhysic/linux-systems-reliability-platform/commit/e49d3595eaa23410223790f5cebf3c4643f10a9e), with per-file hashes in the [source manifest](../examples/robustness/validation/SOURCE_MANIFEST.json).
- **Environment:** dedicated Ubuntu 24.04 WSL2 distribution, Python 3.12.3, systemd 255, cgroup v2, kernel `6.18.54.1-microsoft-standard-WSL2`. See the original [environment record](../examples/robustness/validation/environment.txt).
- **Experiment:** four-second bounded hang request, dispatched at `2026-11-02T03:02:23.561655+00:00`; service resource limits were 25% CPU and 128 MiB memory. The requested fault is harness ground truth, kept separate from diagnostic evidence.

## What the observations establish

The following is a selective summary, with zero-based JSON array references for inspection.

| Stage | Saved evidence | Interpretation |
| --- | --- | --- |
| Healthy baseline | `baseline[0..3]`: four valid `/work` responses; median functional-probe latency **10.34 ms** | The service could perform and return the expected work before the experiment. |
| Requests stop succeeding | `samples[0..5]`: six timeouts, approximately **401 ms** each against a 0.4 s probe deadline | Work requests were unavailable to the observer. |
| Process and listener remain | During those six failures: unit `active/running`, PID **1508**, start ticks **4824**, and one listener on the lab port | A live main process and listening socket coexisted with failed work. A failed request alone does not establish a crash. |
| First response returns | `samples[6]`: valid, normal-mode response, **92.93 ms** latency | It exceeded the trial's 50 ms timely-recovery threshold, so it did not establish the recovery point. |
| Timely work recovers | `samples[7..10]`: four consecutive valid, normal-mode responses from the same process identity | The final stable sequence satisfies the three-response recovery criterion; no subsequent relapse was observed in the trial window. |
| Cleanup completes | `cleanup.ok: true`; timer, recovery unit, and collision helper inactive; five cleanup probe attempts ended with three successes | The two earlier connection-refused attempts remain recorded as transient retries before final verified restoration. |

Process identity includes boot ID, PID, and start ticks; successful work responses are checked against the expected computation and local process identity. The saved trial observed one process identity throughout its baseline/incident/recovery samples. It supports the explanation **“the process remained present while work requests timed out.”** The internal hang mechanism is known from the separate injected-fault record; these observations alone do not identify every internal cause of unresponsiveness.

The recovery point is the first success in the final timely sequence, at monotonic `54.885139143`. Subtracting injection dispatch at `50.363081747` gives **4.522 s dispatch-to-observed-recovery**. The third success confirms that sequence at **5.098 s after dispatch**. Cleanup is a later, separately verified action.

## Compare the four incidents

The [saved comparison](../examples/robustness/validation/comparison.json) aggregates only this robustness run at the source revision above. Each number is `scenarios.<scenario>.metrics.recovery_time_s.median`, rounded to two decimal places.

| Scenario | Recovered trials | Median injection-dispatch-to-observed-recovery |
| --- | --- | --- |
| Crash | 3/3 | 2.79 s |
| Hang | 3/3 | 4.52 s |
| CPU pressure | 3/3 | 4.20 s |
| Port collision | 3/3 | 7.25 s |

These are descriptive lab measurements with **n=3 per incident**, a synthetic workload, a configured fault interval, and a specific restart policy. Dispatch is an experiment reference; exact failure onset remains uncertain. These values are not production MTTR, an SLO, or a reliable tail estimate. Sampling, probe deadlines, sequential collection, and host scheduling affect the observations. Read the [measurement semantics and limitations](EVIDENCE.md) before interpreting the numbers.

Continue with the [diagnostic walkthrough](DIAGNOSTICS.md), [full robustness evidence](../examples/robustness/README.md), or [architecture](ARCHITECTURE.md).
