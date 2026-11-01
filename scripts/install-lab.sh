#!/usr/bin/env bash
# Install ONLY inside a disposable Linux VM/WSL distribution.
set -Eeuo pipefail
if [[ $# != 1 || "$1" != --accept-disposable-lab ]]; then
    echo 'Usage: sudo bash scripts/install-lab.sh --accept-disposable-lab' >&2
    echo 'This explicitly dedicates the current virtual Linux environment to LSRP.' >&2
    exit 2
fi
[[ $EUID == 0 ]] || { echo 'Installation requires root.' >&2; exit 1; }
[[ $(cat /proc/1/comm) == systemd ]] || { echo 'systemd must be PID 1.' >&2; exit 1; }
[[ -f /sys/fs/cgroup/cgroup.controllers ]] || { echo 'cgroup v2 required.' >&2; exit 1; }
virt=$(systemd-detect-virt || true)
case "$virt" in
    wsl|kvm|qemu|vmware|microsoft|oracle|xen|bochs|parallels|bhyve|apple|uml|amazon) ;;
    *) echo "A recognized disposable VM/WSL is required (observed: $virt)." >&2; exit 1 ;;
esac
for executable in python3 systemctl journalctl ss useradd install flock; do
    command -v "$executable" >/dev/null || { echo "Missing executable: $executable" >&2; exit 1; }
done
/usr/bin/python3 -I -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
export LSRP_INSTALL_REPO="$repo"
# Validate existing ownership before any stop, overwrite, account, or directory action.
/usr/bin/python3 -I - <<'PY'
import hashlib, json, os, pathlib, pwd, stat, sys
paths = ['/etc/lsrp', '/opt/lsrp', '/opt/lsrp/lsrp', '/usr/local/bin/lsrp',
         '/run/lsrp', '/run/lsrp/lock', '/etc/lsrp/install.lock',
         '/var/lib/lsrp', '/var/lib/lsrp/service-account.json']
units = ['lsrp-lab.service','lsrp-collision.service','lsrp-recover.service','lsrp-recover.timer']
paths += ['/etc/systemd/system/' + n for n in units]
for raw in paths:
    p = pathlib.Path(raw)
    for q in [p, *p.parents]:
        if q.is_symlink():
            raise SystemExit(f'Refusing symlink: {q}')
        if q.exists():
            s=q.stat()
            if s.st_uid != 0 or s.st_mode & 0o022:
                raise SystemExit(f'Refusing non-root-owned or writable install path: {q}')
            if q != p and not q.is_dir():
                raise SystemExit(f'Refusing non-directory install ancestor: {q}')
marker = pathlib.Path('/etc/lsrp/lab.json')
receipt = pathlib.Path('/var/lib/lsrp/service-account.json')
machine = pathlib.Path('/etc/machine-id').read_text().strip()
try:
    account = pwd.getpwnam('lsrp-lab')
except KeyError:
    account = None
if account is not None and not (account.pw_uid > 0 and account.pw_shell == '/usr/sbin/nologin' and
                                account.pw_dir == '/nonexistent'):
    raise SystemExit('Existing lsrp-lab user has an unexpected UID, home or shell')
receipt_valid = False
if receipt.exists():
    if not receipt.is_file():
        raise SystemExit('Account receipt must be a regular file')
    retained = json.loads(receipt.read_text())
    receipt_valid = (retained.get('schema_version') == 1 and retained.get('account') == 'lsrp-lab' and
                     retained.get('machine_id') == machine and account is not None and
                     retained.get('service_uid') == account.pw_uid)
    if not receipt_valid:
        raise SystemExit('Retained account receipt does not identify this machine/service UID')
if marker.exists() or marker.is_symlink():
    if marker.is_symlink() or marker.stat().st_uid != 0 or marker.stat().st_mode & 0o022:
        raise SystemExit('Unsafe existing lab marker')
    d=json.loads(marker.read_text())
    if d.get('machine_id') != machine or d.get('unit') != 'lsrp-lab.service' or d.get('schema_version') != 1 or account is None or d.get('service_uid') != account.pw_uid:
        raise SystemExit('Existing marker does not identify this LSRP lab')
    # Updating must not stop a replaced unit or execute modified installed code.
    manifest=d.get('sha256')
    bootstrap={'/opt/lsrp/lsrp/__init__.py','/opt/lsrp/lsrp/lab.py'}
    if not isinstance(manifest,dict) or not bootstrap.issubset(manifest):
        raise SystemExit('Existing installation has no complete guard manifest')
    for raw in bootstrap:
        p=pathlib.Path(raw)
        if p.is_symlink() or not p.is_file() or p.stat().st_uid != 0 or p.stat().st_mode & 0o022:
            raise SystemExit('Unsafe guard bootstrap path: '+raw)
        if hashlib.sha256(p.read_bytes()).hexdigest() != manifest[raw]:
            raise SystemExit('Guard bootstrap code changed: '+raw)
    sys.dont_write_bytecode=True
    sys.path.insert(0,'/opt/lsrp')
    from lsrp.lab import guard
    guard()
else:
    for p in paths:
        if p.startswith('/run/lsrp') or p.endswith('/install.lock') or p.startswith('/var/lib/lsrp'):
            continue
        if pathlib.Path(p).exists():
            raise SystemExit(f'Existing unmarked installation path: {p}')
    if account is not None and not receipt_valid:
        raise SystemExit('Existing unmarked lsrp-lab user has no valid retained-account receipt; refusing ownership')
PY
install -d -o root -g root -m 0755 /etc/lsrp /opt/lsrp
exec 9>/etc/lsrp/install.lock
flock -n 9 || { echo 'Another LSRP installation is in progress.' >&2; exit 1; }
# Runtime lock also excludes trials during update; match lab.py's fixed lock.
install -d -o root -g root -m 0755 /run/lsrp
exec 8>/run/lsrp/lock
chmod 0600 /run/lsrp/lock
flock -n 8 || { echo 'An LSRP operation is in progress.' >&2; exit 1; }
if [[ -f /etc/lsrp/lab.json ]]; then
    systemctl stop lsrp-recover.timer lsrp-recover.service lsrp-collision.service lsrp-lab.service
fi
if ! id lsrp-lab >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --comment 'LSRP dedicated lab service' lsrp-lab
fi
install -d -o root -g root -m 0755 /var/lib/lsrp
/usr/bin/python3 -I - <<'PY'
import json, os, pathlib, pwd, tempfile
p=pathlib.Path('/var/lib/lsrp/service-account.json')
data={'schema_version':1,'account':'lsrp-lab',
      'machine_id':pathlib.Path('/etc/machine-id').read_text().strip(),
      'service_uid':pwd.getpwnam('lsrp-lab').pw_uid}
fd, name=tempfile.mkstemp(prefix='.account-', dir=p.parent)
try:
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(name, 0o644)
    os.replace(name, p)
finally:
    if os.path.exists(name):
        os.unlink(name)
PY
install -d -o root -g root -m 0755 /opt/lsrp/lsrp
for source in "$repo"/lsrp/*.py; do
    [[ -f "$source" && ! -L "$source" ]] || { echo 'Unsafe package source.' >&2; exit 1; }
    install -o root -g root -m 0644 "$source" "/opt/lsrp/lsrp/$(basename -- "$source")"
done
for name in lsrp-lab.service lsrp-collision.service lsrp-recover.service lsrp-recover.timer; do
    install -o root -g root -m 0644 "$repo/deploy/$name" "/etc/systemd/system/$name"
done
cat >/usr/local/bin/lsrp <<'PY'
#!/usr/bin/python3 -I
import sys
sys.path.insert(0, '/opt/lsrp')
from lsrp.cli import main
raise SystemExit(main())
PY
chown root:root /usr/local/bin/lsrp
chmod 0755 /usr/local/bin/lsrp
/usr/bin/python3 -I - <<'PY'
import hashlib, json, os, pathlib, pwd, uuid
p=pathlib.Path('/etc/lsrp/lab.json')
old=json.loads(p.read_text()) if p.exists() else {}
data={'schema_version':1,'machine_id':pathlib.Path('/etc/machine-id').read_text().strip(),
      'service_uid':pwd.getpwnam('lsrp-lab').pw_uid,'port':18765,'unit':'lsrp-lab.service',
      'install_id':old.get('install_id',str(uuid.uuid4()))}
artifacts=list(pathlib.Path('/opt/lsrp/lsrp').glob('*.py'))
artifacts += [pathlib.Path('/etc/systemd/system')/name for name in
              ['lsrp-lab.service','lsrp-collision.service','lsrp-recover.service','lsrp-recover.timer']]
for artifact in artifacts:
    if artifact.is_symlink() or artifact.stat().st_uid != 0 or artifact.stat().st_mode & 0o022:
        raise SystemExit('Unsafe installed artifact: '+str(artifact))
data['sha256']={str(a):hashlib.sha256(a.read_bytes()).hexdigest() for a in artifacts}
temp=p.with_suffix('.tmp')
with temp.open('x') as f:
    json.dump(data,f,indent=2)
    f.write('\n')
os.chmod(temp,0o644)
os.replace(temp,p)
PY
systemctl daemon-reload
systemctl reset-failed lsrp-lab.service lsrp-collision.service lsrp-recover.service || true
systemctl start lsrp-lab.service
echo 'LSRP installed. Run: lsrp doctor; lsrp status; sudo lsrp run crash --output crash.json'
