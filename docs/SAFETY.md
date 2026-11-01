# Lab boundaries and cleanup

Fault experiments run only in a deliberately provisioned disposable Linux VM or dedicated WSL2 distribution. The normal service is unprivileged; the operator needs root to install fixed units and perform restricted service-manager operations.

## Installation boundary

Installation requires `--accept-disposable-lab`, real systemd as PID 1, and cgroup v2. The installer checks virtualization, copies code into the fixed root-owned `/opt/lsrp` location, installs the named systemd units, creates the `lsrp-lab` account, and writes `/etc/lsrp/lab.json`. The marker records the machine identity, installation UUID, fixed unit/port, and service UID.

The acceptance flag is a declaration by the operator that this machine is a disposable lab. Virtualization detection cannot prove that a VM contains no important data. Do not install it into a shared development distribution, production VM, or bare-metal host.

## Mutation boundary

Before disruptive work, the CLI verifies the marker, installation ownership, systemd/cgroup environment, and intended service identity. Unsafe permissions, symlinks, missing marker data, stale identity, ambiguous ownership, or failed preconditions prevent mutation. Operations are locked so that two fault runs cannot knowingly modify the same service concurrently.

The CLI exposes no arbitrary target-PID, host, port, namespace, or unit parameter. Only `lsrp-lab.service`, `lsrp-collision.service`, the fixed control socket, and `127.0.0.1:18765` belong to this lab. It never changes external routes or firewall rules, runs host-wide process kills, or intentionally exhausts host memory.

## Independent limits

| Mechanism | Boundary |
| --- | --- |
| Dedicated service UID | Normal requests and injected service work do not run as root |
| Root-owned code/configuration/units | Service UID cannot replace trusted control definitions |
| Unix peer authentication | Fault mode changes require an authorized root peer |
| CPU quota | Synthetic demand is constrained within the service's cgroup |
| Memory/task/FD ceilings | Bound the service's resource envelope |
| Monotonic fault TTL | Hang/CPU modes restore themselves even if the runner disappears |
| systemd helper runtime limit | Collision listener expires independently of runner cleanup |
| Fixed recovery timer | A separately scheduled systemd recovery action restores the main service if the fault runner disappears |
| Trial deadline and signal handling | Normal interruption enters cleanup and writes a result |
| Identity validation | Unrelated listener responses cannot count as healthy work |

Exact unit values are visible in `deploy/` and through `systemctl cat`. Resource-control directives depend on the local kernel/systemd combination; validation checks actual cgroup evidence instead of assuming the presence of a unit file makes a limit effective.

## Interruption and restoration

On ordinary failure or SIGINT/SIGTERM, the runner removes service fault modes, stops the collision helper, restores service operation as appropriate, verifies normal work, and records cleanup errors. If a process is forcibly killed, it cannot execute a finally block; fault TTLs and helper limits provide a second boundary. After interruption, run:

```bash
sudo python3 -m lsrp cleanup
python3 -m lsrp status --json
systemctl is-active lsrp-collision.service
```

An inactive collision helper is expected. Its `is-active` command returns nonzero when inactive; this is normal for that inspection command. Check the work response and inspect the saved record before starting another trial.

To shut down or remove the installation:

```bash
sudo python3 -m lsrp stop
sudo bash scripts/uninstall-lab.sh --accept-disposable-lab
```

The uninstaller is limited to the owned lab installation and retains the locked service account for safe UID ownership continuity on reinstall. Preserve wanted incident artifacts separately. Discarding the entire dedicated VM/distribution is an additional operator-controlled option; the project does not automate deletion of unrelated virtual machines or WSL distributions.

## Trust model and remaining limits

The boundary assumes trusted operator/root access and a normal Linux kernel/service manager. It does not defend against malicious root, a compromised kernel, or privileged host software. It constrains this laboratory's actions and catches mistaken targets. The WSL2 distribution shares a VM kernel and host resources with other distributions, so service-local cgroup limits matter; it is not a separate physical machine. Prefer a dedicated VM when stronger isolation is required.

Collectors avoid process environments, arbitrary files, and host-wide journals. Bounded evidence can still reveal machine identity, platform details, and operational timing. Inspect artifacts before publication. Missing permissions are reported explicitly; granting unrestricted root to the service is not an acceptable workaround.
