#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# == 1 && "$1" == --accept-disposable-lab ]] || { echo 'Usage: sudo bash scripts/uninstall-lab.sh --accept-disposable-lab' >&2; exit 2; }
[[ $EUID == 0 ]] || { echo 'Uninstallation requires root.' >&2; exit 1; }
# Check before controlling units or removing anything; never remove arbitrary trees.
/usr/bin/python3 -I - <<'PY'
import json, pathlib, pwd
p=pathlib.Path('/etc/lsrp/lab.json')
for q in [p,*p.parents]:
    if q.is_symlink() or (q.exists() and (q.stat().st_uid != 0 or q.stat().st_mode & 0o022)):
        raise SystemExit('Unsafe marker path')
d=json.loads(p.read_text())
if d.get('machine_id') != pathlib.Path('/etc/machine-id').read_text().strip() or d.get('unit') != 'lsrp-lab.service' or d.get('service_uid') != pwd.getpwnam('lsrp-lab').pw_uid:
    raise SystemExit('Marker does not identify this lab')
for raw in ['/opt/lsrp','/opt/lsrp/lsrp','/usr/local/bin/lsrp', '/run/lsrp', '/run/lsrp/lock']:
    p=pathlib.Path(raw)
    if p.is_symlink() or (p.exists() and (p.stat().st_uid != 0 or p.stat().st_mode & 0o022)):
        raise SystemExit('Unsafe installed path: '+raw)
PY
install -d -o root -g root -m 0755 /run/lsrp
exec 8>/run/lsrp/lock
flock -n 8 || { echo 'An LSRP operation is in progress.' >&2; exit 1; }
systemctl stop lsrp-recover.timer lsrp-recover.service lsrp-collision.service lsrp-lab.service
systemctl disable lsrp-lab.service lsrp-recover.timer >/dev/null 2>&1 || true
/usr/bin/python3 -I - <<'PY'
from pathlib import Path
# Exact artifacts only; unexpected files remain and are reported by rmdir.
for name in ['lsrp-lab.service','lsrp-collision.service','lsrp-recover.service','lsrp-recover.timer']:
    p=Path('/etc/systemd/system')/name
    if p.is_symlink() or p.stat().st_uid != 0:
        raise SystemExit('Unsafe unit path: '+str(p))
    p.unlink()
for p in Path('/opt/lsrp/lsrp').glob('*.py'):
    if p.is_symlink() or p.stat().st_uid != 0:
        raise SystemExit('Unsafe code path: '+str(p))
    p.unlink()
cache=Path('/opt/lsrp/lsrp/__pycache__')
if cache.is_dir() and not cache.is_symlink():
    for p in cache.glob('*.pyc'):
        if p.is_symlink() or p.stat().st_uid != 0:
            raise SystemExit('Unsafe cache path')
        p.unlink()
    cache.rmdir()
Path('/usr/local/bin/lsrp').unlink()
Path('/etc/lsrp/lab.json').unlink()
Path('/etc/lsrp/install.lock').unlink(missing_ok=True)
Path('/opt/lsrp/lsrp').rmdir()
Path('/opt/lsrp').rmdir()
Path('/etc/lsrp').rmdir()
PY
systemctl daemon-reload
echo 'LSRP artifacts removed; the locked service account is retained to avoid UID reuse.'
