# Dedicated Windows/WSL2 lab

`scripts/wsl_lab.py` provisions a new, named, disposable Linux distribution and transfers source without mounting Windows drives inside it. Run the helper from Windows PowerShell with Python 3.11+ and an existing WSL2 installation. It uses only Python's standard library and `wsl.exe`.

## Create and validate

```powershell
python scripts/wsl_lab.py create
python scripts/wsl_lab.py validate --run baseline-validation
python scripts/wsl_lab.py fetch --run baseline-validation
```

Creation uses the fixed distribution name **`LSRP-Lab`**. If that name already exists, `create` fails without replacing it. The helper never unregisters a distribution. Existing Ubuntu, Docker, and other WSL distributions remain untouched.

The default runtime directory is `~/lsrp-lab-runtime`, outside the checkout. It stores the rootfs download, virtual disk, source manifest, and fetched evidence. To choose a different directory under your Windows home directory, put `--runtime-dir` before the command and use the same option on subsequent commands:

```powershell
python scripts/wsl_lab.py --runtime-dir "$env:USERPROFILE\lsrp-runtime-alternate" create
python scripts/wsl_lab.py --runtime-dir "$env:USERPROFILE\lsrp-runtime-alternate" validate --run first-run
python scripts/wsl_lab.py --runtime-dir "$env:USERPROFILE\lsrp-runtime-alternate" fetch --run first-run
```

An alternative runtime directory does not create an alternative distribution name; there is one fixed LSRP lab per Windows account. Runtime paths inside the source checkout, paths containing the checkout, and the home directory itself are refused.

## Image and configuration

The pinned rootfs is the [official Ubuntu Noble WSL image](https://cloud-images.ubuntu.com/wsl/releases/noble/current/). The exact image filename and SHA256 are:

```text
ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz
8251e27ffff381a4af5f41dcb94d867de3e0d9774a9241908ab34555d99315ea
```

The helper checks the published `SHA256SUMS` over official HTTPS before downloading, then checks the downloaded bytes against that pin. A changed upstream checksum causes a failure requiring deliberate review of an image update. A cached image is reused only if it matches the pin; a mismatching file is not overwritten. The image is imported as WSL version 2 using the documented [custom distribution import](https://learn.microsoft.com/en-us/windows/wsl/use-custom-distro) mechanism.

The new distribution receives this `/etc/wsl.conf`:

```ini
[boot]
systemd=true
[automount]
enabled=false
mountFsTab=false
[interop]
enabled=false
appendWindowsPath=false
```

Only the newly created distribution is terminated once to apply its configuration. There is no `wsl --shutdown`, no global `.wslconfig` write, and no restart of another named distribution. Systemd, Python, and `iproute2` prerequisites are installed inside the new lab. Configuration follows [Microsoft's WSL settings documentation](https://learn.microsoft.com/en-us/windows/wsl/wsl-config).

Disabling interoperability means Windows executables are unavailable from inside this Linux lab; the Windows-side helper can still invoke `wsl.exe` to enter it. Disabling drive automount keeps the project transfer independent of `/mnt/c`. The first fresh import starts once to receive this configuration, before any fault experiments are installed or run.

## Source synchronization

```powershell
python scripts/wsl_lab.py sync --install
python scripts/wsl_lab.py validate --run after-change
python scripts/wsl_lab.py fetch --run after-change
```

`sync` is the explicit operation for an existing helper-owned lab. The helper requires its creation marker `/etc/lsrp-wsl-owned` and verifies the isolation configuration before updates. It will not adopt an arbitrary pre-existing distribution merely because its name matches. `--install` also refreshes the installed root-owned service/CLI; omit it only when you intentionally want to transfer source without changing the installation.

Source files are selected from the project code, tests, scripts, units, documentation, and known root metadata. Git internals, virtual environments, caches, generated artifacts/results, environment files, and key files are excluded. Symlink source entries are refused. A tar stream is sent directly to Linux stdin. A clean staged source tree replaces `/root/lsrp-source`; the previous tree is preserved as a uniquely named `/root/lsrp-source-backup-*` directory. Stale deleted Python modules cannot silently remain in the active tree. Backups are retained for operator inspection; the helper does not delete them automatically.

Each transfer includes:

- `SOURCE_REVISION`: Git commit when available, otherwise an explicit unavailable/uncommitted marker.
- `SOURCE_MANIFEST.json`: SHA256 of every transferred source file, a digest of the canonical file/hash map, source revision, dirty-state metadata, and creation timestamp.

Validation recomputes the actual source hashes. A transferred manifest that no longer matches causes a failure requiring synchronization. The manifest and revision are copied into the evidence directory and referenced in `environment.txt`, even though `.git` is deliberately absent inside Linux. Dirty or uncommitted trees remain identified by their actual file hashes; do not represent their run as validation of a clean commit.

## Results and failures

Validation stores evidence at `/root/lsrp-results/RUN_NAME`. Run names are restricted to a short safe identifier. Use a new name for every run; neither validation nor fetching overwrites prior evidence. `fetch` copies only expected regular evidence files into `~/lsrp-lab-runtime/results/RUN_NAME`, rejects traversal and link entries, and does not execute downloaded content. With no `--run`, it fetches the most recently requested validation, including a failed one.

The helper propagates subprocess failures. A failed creation preserves the partial new lab rather than deleting it. A failed validation keeps whatever artifacts were recorded; fetch them before investigating. If creation failed before the helper marker was written, inspect that new distribution manually. A future `create` still refuses to overwrite it. No automatic removal is attempted.

To inspect the service directly from Windows:

```powershell
wsl.exe -d LSRP-Lab -u root --exec /usr/local/bin/lsrp doctor --json
wsl.exe -d LSRP-Lab -u root --exec /usr/local/bin/lsrp status --json
```

## Isolation limits

WSL2 distributions share a kernel and host VM resources. A dedicated distribution isolates this project's filesystem/service installation and disables host-drive access through ordinary automount, but does not make it a separate physical host. The service's actual cgroup CPU/memory/task limits remain essential. Use a dedicated conventional VM for stronger isolation. The helper changes no global WSL memory/CPU settings; any resource policy chosen by the Windows operator remains outside this project.

No host-wide memory exhaustion, external network changes, or arbitrary process targeting is part of the lab. Normal cleanup restores the owned Linux service; removing a whole WSL distribution is an independent operator action and is intentionally absent from this helper.
