# Recorded Linux evidence

Start with the **[latest robustness verification](robustness/README.md)** or the **[real hang-incident walkthrough](../docs/DEMO.md)**. These are saved observations from a dedicated Ubuntu 24.04 WSL2 lab, not generated fixtures or hosted-CI integration results.

## Latest validated run

The validated source is revision [`e49d359`](https://github.com/ThePhysic/linux-systems-reliability-platform/commit/e49d3595eaa23410223790f5cebf3c4643f10a9e), recorded with exact file hashes in [SOURCE_MANIFEST.json](robustness/validation/SOURCE_MANIFEST.json). The lab used Python 3.12.3, systemd 255, cgroup v2, and the kernel recorded in [environment.txt](robustness/validation/environment.txt). The service ran unprivileged under a 25% CPU quota and 128 MiB memory limit.

- [Linux test log](robustness/validation/unit-tests.txt): **100 tests passed**; the separate [unprivileged test log](robustness/nonroot-unit-tests.txt) also records 100 passing tests.
- [Validation summary](robustness/validation/validation-summary.json): **12/12 recovered trials**, three per scenario, plus lifecycle, evidence, chronology, recovery, and cleanup checks.
- [Interruption validation](robustness/interruption/validation.json): **8/8 checks passed**, including exclusive-operation rejection, SIGTERM cleanup, honest SIGKILL checkpoint preservation, and independent timer-driven restoration.
- [Comparison](robustness/validation/comparison.json) and [raw trial records](robustness/validation/trials/): per-scenario timing distributions and the observations behind them.
- [Paired overhead measurements](robustness/validation/overhead.json): three workload comparisons with and without full observation.

Inspect the saved records from the repository root, without root or a live lab:

```bash
python3 -m lsrp report examples/robustness/validation/trials/hang-325476e5-045c-4fa9-96ef-4d7d4c803f65.json
python3 -m lsrp compare examples/robustness/validation/trials --json
```

On Windows PowerShell, use `python` in place of `python3`; the [demo](../docs/DEMO.md) includes copy-paste commands for both platforms.

The intentionally interrupted trials remain failed/inconclusive even though the separate recovery validator passed. The [independent observations](robustness/interruption/independent-recovery-observations.json) preserve timer/service/process evidence. Restoration after a killed runner does not rewrite that runner's incomplete claim.

The robustness archive is preserved unchanged. Current report presentation can differ from the validated source; the saved raw observations and manifests remain the provenance for these results. Three trials per incident are descriptive, and timings are relative to injection dispatch rather than exact failure onset. See [evidence and measurement semantics](../docs/EVIDENCE.md).

## Historical reference run

The earlier [validation summary](validation/validation-summary.json), [comparison](validation/comparison.json), and [interruption record](interruption/validation.json) remain available for historical inspection. That run passed 83 Linux unit tests and 12 incident trials at source revision `da56e81`, identified by its [own manifest](validation/SOURCE_MANIFEST.json). It is separate from the later robustness run above.

Both archives preserve all trials from their named runs and the deliberately interrupted records. The deadline-aware probe transport changed client overhead between revisions, so combining the two archives into one benchmark would obscure that change. Earlier unsuccessful development runs remain in the local lab runtime; no raw records have been rewritten to make their outcomes look successful.
