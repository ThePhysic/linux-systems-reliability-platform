# Recorded Linux evidence

These files are actual observations from the dedicated `LSRP-Lab` WSL2 distribution on 2026-11-01. They are not generated fixtures. The lab used Ubuntu 24.04, Python 3.12.3, systemd 255, cgroup v2, and the kernel recorded in [environment.txt](validation/environment.txt). The service ran unprivileged under a 25% CPU quota and 128 MiB memory limit.

[Full validation](validation/validation-summary.json) passed all 12 trials (three per scenario), normal/graceful lifecycle checks, evidence/chronology checks, functional recovery and cleanup, plus three [paired overhead measurements](validation/overhead.json). All 83 Linux unit tests passed; see [the complete test log](validation/unit-tests.txt). The [source manifest](validation/SOURCE_MANIFEST.json) identifies exact transferred file hashes and revision `da56e81`, which includes all five initial Astra review fixes. Subsequent evidence and documentation commits do not change that incident implementation.

| Scenario | Trials recovered | Median injection-to-recovery (seconds) |
| --- | --- | --- |
| Crash | 3/3 | 2.847 |
| Hang | 3/3 | 4.343 |
| CPU pressure | 3/3 | 4.198 |
| Port collision | 3/3 | 7.250 |

These descriptive timings come from [comparison.json](validation/comparison.json). The reference is injection dispatch, not exact failure onset; each record preserves observation bounds. Three samples do not justify a stable tail estimate or production availability claim. Baseline/fault workloads are deliberately synthetic. Probe latency includes HTTP handling, response validation and local process-identity verification.

Inspect any complete raw [trial JSON](validation/trials/) with the offline CLI:

```bash
python -m lsrp compare examples/validation/trials --json
python -m lsrp report examples/validation/trials/CRASH_FILENAME.json
```

Replace `CRASH_FILENAME.json` with an actual filename from that directory. No Linux privileges are needed to read these records.

[Interruption validation](interruption/validation.json) passed eight checks: it demonstrated exclusive-operation rejection, SIGTERM cleanup, a SIGKILL-preserved incomplete record, and timer-driven service restoration after the runner died. Its [independent observations](interruption/independent-recovery-observations.json) preserve timer/service/process evidence. The intentionally interrupted trials remain failed/inconclusive, even though the separate recovery validator passed. These are expected outcomes, not discarded failed samples.

The original unsuccessful development runs remain in the local lab runtime and concern earlier implementations. This directory preserves every trial from the named final validation run, plus both deliberately interrupted trials.
