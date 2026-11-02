# Validation and reproducibility

To test interrupted runners separately after installation, run:

```bash
sudo /usr/bin/python3 -I scripts/validate-interruption.py /root/lsrp-results/interruption-run
```

Use a fresh output directory. This test deliberately terminates its own hang runner, rejects overlapping mutation, then kills its own collision runner and observes the independent recovery timer. The killed trial must remain incomplete even after the service recovers. Checks and observations are saved in `validation.json` and adjacent files; final cleanup is attempted on every exit.

There are two different forms of evidence: automated checks of code semantics and end-to-end observations from a real Linux lab. Both are needed. Mocks of `systemctl`, fabricated `/proc` files, and portable HTTP tests cannot prove the actual unit restarted or the kernel enforced a quota.

## Portable checks

From the repository root on Linux or Windows:

```bash
python -m unittest discover -s tests -v
python -m compileall -q lsrp tests
```

Some service-specific checks require Linux facilities such as Unix peer credentials. Those tests state a platform skip when unavailable. A passing Windows job establishes portable behavior only. CI runs supported Python versions on Ubuntu and Windows; no CI label implies a real systemd incident has been executed unless that integration workflow actually ran.

## Real Linux validation

Provision a dedicated VM/WSL2 lab and install it as described in the README. Then run:

```bash
sudo bash scripts/validate-linux.sh artifacts/validation
```

The script uses `set -Eeuo pipefail`, preserves command outputs, and runs CLI commands from the current checkout. It collects platform context, runs unit tests, verifies graceful stop and normal startup, records three trials for every core incident, runs the overhead comparison, validates trial evidence and sustained restoration, and writes `validation-summary.json` only after all required checks pass. A trap attempts cleanup on failure; a successful cleanup does not hide the failing command's status.

Expected artifact types include:

| Artifact | Evidence |
| --- | --- |
| `environment.txt` | Kernel, OS, systemd/Python versions, revision when available |
| `SOURCE_REVISION`, `SOURCE_MANIFEST.json` | Recorded revision/dirty state and hashes of the actual source files validated |
| `unit-tests.txt` | Actual automated-test output |
| `doctor.json` | Actual preflight check result |
| `lifecycle-*.json` / journal | Before/after lifecycle observations |
| `trials/` | Raw incident records and any CLI-generated reports |
| `comparison.json` | Aggregate from saved records |
| `overhead.json` | Repeated baseline/observed workload measurements |
| `validation-summary.json` | Passed record and lifecycle assertions with counts |
| `validation-exit.json` | Final script status and final cleanup outcome, including on failure |

Check artifact paths from the final script version. Generated directories are intentionally excluded from source control by default. A curated reference run can be included under a dedicated examples/evidence path after review, with its source revision and an explicit statement that numbers describe one environment. No reference output should be invented or pre-filled.

For Windows, [the dedicated WSL helper](WSL.md) provisions the lab and transfers source without drive mounts. The transferred source manifest is verified before running tests, and validation records its digest even when `.git` was not copied. Without a transferred manifest, validation generates one from the actual checkout and explicitly records unavailable Git metadata.

## What an incident record must establish

1. Its baseline work requests belong to the intended service.
2. At least one relevant symptom or kernel/service evidence confirms the requested incident occurred; a command's successful return is insufficient.
3. Evidence retains source and timestamps, including errors and missing sources.
4. Recovery includes at least three consecutive identity-valid successful work requests, with the fault cleared.
5. The collision helper has stopped, fault modes are normal, and cleanup verification passes.
6. Failed/inconclusive results remain visible and cause a failing validation run.

The suite repeats each scenario three times to catch obvious contaminated state and lifecycle bugs. It records small-sample distributions rather than making tail-performance claims. Resource and event counters are evaluated only within compatible process/cgroup lifetimes.

## Manual integration workflow

The optional GitHub workflow `linux-lab.yml` uses a **dedicated disposable self-hosted Linux VM runner** explicitly labeled `lsrp-disposable`. Its workflow-dispatch input requires acknowledgment of disposable lab use. Do not attach that label to a general workstation or production runner. The workflow installs the fixed lab, executes real validation, uploads artifacts even on failure, and tears down its installation. Hosted unit-test runners are used only for portable checks.

Before attaching a self-hosted runner, consider the repository's trust and permissions. The workflow is manual and requires the lab declaration; repository write access must be trusted because the workflow executes checked-out code as root in that disposable VM.

## Interpreting results

An all-passed run supports this source revision on the recorded kernel, systemd, cgroup hierarchy, and VM configuration. It does not imply compatibility with every distribution, production high availability, or immunity from sampling races. Review any missing data warnings even when functional recovery succeeds. Use the diagnostic walkthrough to cross-check a selected incident against real commands.

If a required Linux facility is unavailable, stop and report that limitation. Do not mark mocked evidence as real integration, replace unknown values with zero, or use the fault label as proof of diagnosis.

## Published reference evidence

The [robustness archive](../examples/robustness/README.md) remains an original record of validated source `e49d359`. The later portfolio polish changes report wording and its regression tests; it does not change experiment timing, measurement, recovery, identity, or service-manager behavior. All other tracked files under `lsrp/`, `tests/`, `deploy/`, and `scripts/` still match that archive's manifest. Portable tests and offline report commands verify this presentation pass; it is not a new privileged Linux integration run.

An inspection of the tracked example files found no credential-like material. The public evidence does retain operational correlation metadata: the lab hostname in [environment.txt](../examples/robustness/validation/environment.txt), persistent machine/hostname fields, boot/process IDs and timestamps in [lifecycle-journal.jsonl](../examples/robustness/validation/lifecycle-journal.jsonl), and the installation UUID and lab-only `/root` paths in [interruption validation](../examples/robustness/interruption/validation.json). These are identifying metadata rather than authentication credentials. The raw records and manifests have not been sanitized or rewritten; a future privacy-oriented excerpt must be labeled separately so its relationship to the original evidence remains clear.
