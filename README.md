# Linux Systems & Reliability Platform

LSRP is a terminal-based Linux reliability laboratory. A real, unprivileged network service runs under systemd. The CLI introduces bounded failures, collects operating-system evidence, and proves recovery by making successful work requests.

The question behind each experiment is: **What can I prove is happening to this service, and how do I know it recovered?**

## What you can demonstrate

| Experiment | What to investigate |
| --- | --- |
| `crash` | Process identity changes, signal/exit evidence, restart policy, and request unavailability |
| `hang` | A live process and listener while readiness/work requests fail |
| `cpu` | Service-local CPU pressure, cgroup throttling counters, and request latency |
| `collision` | An owned conflicting listener, bind failure, and startup recovery |

Reports separate observations, hypotheses, experiment ground truth, recovery actions, and limitations. Missing or inaccessible data stays missing. A systemd `active` state or a replacement PID alone cannot establish recovery.

The scope is one service, one CLI, and these four incidents. A web dashboard, memory/FD exhaustion, eBPF, and advanced networking experiments are deferred.

## Requirements

- A **disposable Linux VM or dedicated WSL2 distribution**, with systemd running as PID 1.
- Python 3.11 or newer, systemd/journald, cgroup v2 with CPU and memory controllers, and `ss` from `iproute2`.
- Root for installation, service control, and fault experiments. The service itself runs as `lsrp-lab`, without root privileges.

The implementation uses Python's standard library. Application dependencies, Docker, Kubernetes, and a database are unnecessary. Windows can run the portable unit tests and read saved reports; Linux service experiments run inside the lab.

## Windows: create a dedicated WSL2 lab

From PowerShell in this checkout, with WSL2 already available:

```powershell
python scripts/wsl_lab.py create
python scripts/wsl_lab.py validate --run first-run
python scripts/wsl_lab.py fetch --run first-run
```

The helper downloads a checksum-pinned official Ubuntu image, imports a **new** distribution named `LSRP-Lab`, disables Windows-drive automount and Windows executable interoperability in that distribution, enables systemd, transfers source through stdin, and installs the lab. It refuses to overwrite an existing distribution and never unregisters one. Runtime files and fetched evidence live in `~/lsrp-lab-runtime`, outside this repository. It does not change global `.wslconfig` settings or other distributions.

After code changes, use `python scripts/wsl_lab.py sync --install`, then validate with a new run name. The helper manages only a distribution carrying its own creation marker. See [Windows/WSL instructions](docs/WSL.md) for options, boundaries, and source-hash provenance.

## Quickstart

Run these commands **inside the disposable Linux lab**, from a checkout of this repository. On Ubuntu, install the prerequisites first:

```bash
sudo apt-get update
sudo apt-get install -y python3 systemd iproute2
python3 --version
ps -p 1 -o comm=
test -f /sys/fs/cgroup/cgroup.controllers
```

Install the fixed units and root-owned service code, then inspect the environment:

```bash
sudo bash scripts/install-lab.sh --accept-disposable-lab
python3 -m lsrp doctor --json
sudo python3 -m lsrp start
python3 -m lsrp status --json
```

Record one incident and read its report:

```bash
sudo python3 -m lsrp run hang --duration 4 --output artifacts/hang.json
python3 -m lsrp report artifacts/hang.json
```

Compare repeated trials and measure the observer's effect:

```bash
sudo python3 -m lsrp suite --trials 3 --duration 4 --output artifacts/trials
python3 -m lsrp compare artifacts/trials --json
sudo python3 -m lsrp overhead --trials 3 --duration 4 --output artifacts/overhead.json
```

`--duration` controls the bounded fault interval, not the entire trial. Baseline, supervision, cleanup, and recovery checks add time. A failed or inconclusive experiment returns a nonzero exit status and preserves its record; inspect the saved report before rerunning.

For an end-to-end verification with saved evidence:

```bash
sudo bash scripts/validate-linux.sh artifacts/validation
```

The script runs meaningful automated tests, checks normal lifecycle, executes three trials of each incident, checks records and sustained recovery, and records observation overhead. See [validation](docs/VALIDATION.md) for artifact interpretation and the distinction between portable tests and real Linux integration.

## Commands

| Command | Purpose | Privilege |
| --- | --- | --- |
| `doctor [--json]` | Check platform, tools, and lab prerequisites | Read only |
| `status [--json]` | Collect one correlated evidence snapshot | Read only; some evidence may require root |
| `start`, `stop` | Operate the fixed lab service | Root |
| `run SCENARIO --output FILE` | Record one controlled incident | Root |
| `suite --output DIR` | Run all four incidents repeatedly | Root |
| `report FILE [--json]` | Explain a saved incident | Read only; works offline |
| `compare FILE_OR_DIR... [--json]` | Aggregate saved trial measurements | Read only; works offline |
| `overhead --output FILE` | Compare workload with and without collection | Root |
| `cleanup` | Remove active fault conditions and restore the lab | Root |

Use `python3 -m lsrp --help` and command-specific `--help` for options. If installed as a Python package, the `lsrp` console script is equivalent.

## Boundaries and cleanup

The lab binds only `127.0.0.1:18765`. Fault control accepts only fixed lab units and authenticated local control requests. The marker `/etc/lsrp/lab.json` binds the installation to its machine identity. Mutations require verified ownership and are serialized. Service resource limits and fault deadlines provide independent containment if a CLI run is interrupted.

```bash
sudo python3 -m lsrp cleanup
sudo python3 -m lsrp stop
sudo bash scripts/uninstall-lab.sh --accept-disposable-lab
```

Keep the lab isolated from production workloads. The safety checks are engineering boundaries for this laboratory, not a guarantee against a malicious root user. See [safety](docs/SAFETY.md) for preconditions, interruption behavior, and remaining limits.

## How it works

```mermaid
flowchart LR
    CLI[Operator CLI] --> Harness[Bounded experiment harness]
    Harness --> Systemd[systemd]
    Harness --> Control[Authenticated Unix control socket]
    Systemd --> Service[Unprivileged lab service]
    Control --> Service
    Service --> Probes[Identity-validated work probes]
    Kernel[proc / cgroup v2 / sockets] --> Observer[Read-only observer]
    Systemd --> Observer
    Journal[journald] --> Observer
    Probes --> Observer
    Observer --> Record[Timestamped trial JSON]
    Record --> Report[Evidence analysis and report]
```

Read the [architecture](docs/ARCHITECTURE.md), [diagnostic walkthrough](docs/DIAGNOSTICS.md), and [evidence and measurement semantics](docs/EVIDENCE.md). The original [idea package](docs/concept/README.md) is preserved for scope traceability.

## Development

Actual Linux run records, measurements, and interruption evidence are checked in under [examples](examples/README.md).

An independent Astra medium adversarial review and rereview resolved five findings and finished clean; see [the review record](docs/REVIEW.md).

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q lsrp tests
```

Unit tests exercise parsers, diagnosis, measurement semantics, identity validation, service behavior, and CLI contracts. They do not establish that systemd restart or cgroup controls work on a particular host; that requires the Linux validation run.

No performance improvement is promised. Any example numbers must come from recorded experiments, with their environment, sample counts, sampling limits, and source revision. Successful laboratory trials demonstrate this implementation in that environment; they do not establish production high availability.
