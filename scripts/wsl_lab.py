#!/usr/bin/env python3
"""Provision and use only the fixed disposable LSRP WSL2 lab (Windows host)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
from urllib.request import urlopen
import uuid


DISTRO = 'LSRP-Lab'
IMAGE = 'ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz'
IMAGE_BASE = 'https://cloud-images.ubuntu.com/wsl/releases/noble/current/'
IMAGE_SHA256 = '8251e27ffff381a4af5f41dcb94d867de3e0d9774a9241908ab34555d99315ea'
SOURCE = '/root/lsrp-source'
RESULTS = '/root/lsrp-results'
OWNER_TAG = 'lsrp-disposable-wsl-v1'
WSL_CONFIG = '[boot]\nsystemd=true\n[automount]\nenabled=false\nmountFsTab=false\n[interop]\nenabled=false\nappendWindowsPath=false\n'
SOURCE_DIRS = {'lsrp', 'tests', 'deploy', 'scripts', 'docs', '.github'}
SOURCE_ROOT_FILES = {'README.md', 'CONTRACT.md', 'pyproject.toml', '.gitignore', '.gitattributes', 'LICENSE'}
IGNORED_PARTS = {'__pycache__', '.pytest_cache', '.venv', '.git', 'results', 'artifacts'}
FETCH_FILES = {
    'environment.txt', 'unit-tests.txt', 'doctor.json', 'start.txt', 'stop.txt',
    'restart.txt', 'lifecycle-before.json', 'lifecycle-stopped.json', 'lifecycle-after.json',
    'lifecycle-invocation.txt', 'lifecycle-journal.jsonl', 'comparison.json',
    'overhead.json', 'overhead.txt', 'suite.txt', 'cleanup.txt',
    'validation-summary.json', 'validation-exit.json', 'SOURCE_MANIFEST.json', 'SOURCE_REVISION',
}


def checked(command, *, input=None, capture=False, timeout=180):
    return subprocess.run(command, input=input, stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, check=True, timeout=timeout)


def wsl(*command, input=None, capture=False, timeout=180):
    return checked(['wsl.exe', '-d', DISTRO, '-u', 'root', '--exec', *command],
                   input=input, capture=capture, timeout=timeout)


def distros():
    raw = checked(['wsl.exe', '--list', '--quiet'], capture=True).stdout
    text = raw.decode('utf-16-le' if b'\x00' in raw else 'utf-8-sig')
    return {line.strip().lstrip('\ufeff') for line in text.splitlines() if line.strip()}


def runtime_directory(value, repository):
    location = Path(value).expanduser().resolve()
    home = Path.home().resolve()
    if location == home or home not in location.parents:
        raise ValueError('Runtime directory must be a subdirectory of your home directory.')
    repository = Path(repository).resolve()
    if location == repository or repository in location.parents or location in repository.parents:
        raise ValueError('Runtime directory must be outside the repository and cannot contain it.')
    return location


def checksum_entry(text):
    matches = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip('*') == IMAGE:
            matches.append(parts[0].lower())
    if len(matches) != 1 or not re.fullmatch(r'[a-f0-9]{64}', matches[0]):
        raise ValueError('Official SHA256SUMS did not contain one unambiguous image checksum.')
    return matches[0]


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download_image(runtime):
    runtime.mkdir(parents=True, exist_ok=True)
    image = runtime / IMAGE
    if image.exists():
        if image.is_symlink() or sha256_file(image) != IMAGE_SHA256:
            raise ValueError('Existing image does not match the pinned checksum; refusing to overwrite it.')
        return image
    with urlopen(IMAGE_BASE + 'SHA256SUMS', timeout=30) as response:
        sums = response.read(1024 * 1024 + 1)
    if len(sums) > 1024 * 1024 or checksum_entry(sums.decode('utf-8')) != IMAGE_SHA256:
        raise ValueError('Official checksum differs from the pinned image. Review a deliberate image update first.')
    partial = runtime / (IMAGE + '.part-' + uuid.uuid4().hex)
    try:
        digest = hashlib.sha256()
        total = 0
        with urlopen(IMAGE_BASE + IMAGE, timeout=60) as response, partial.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 1024 * 1024 * 1024:
                    raise ValueError('Image exceeded the one GiB download bound.')
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != IMAGE_SHA256:
            raise ValueError('Downloaded image failed SHA256 verification.')
        partial.rename(image)
    finally:
        if partial.exists():
            partial.unlink()
    return image


def source_archive(repository):
    repository = Path(repository).resolve()
    files = {}
    for path in sorted(repository.rglob('*')):
        relative = path.relative_to(repository)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if relative.parts[0] not in SOURCE_DIRS and relative.as_posix() not in SOURCE_ROOT_FILES:
            continue
        if path.is_symlink():
            raise ValueError('Refusing a symlink in the source archive: ' + relative.as_posix())
        if not path.is_file() or path.suffix in {'.pyc', '.pyo', '.pem', '.key'} or path.name.startswith('.env'):
            continue
        files[relative.as_posix()] = path.read_bytes()
    if not {'lsrp/__main__.py', 'scripts/validate-linux.sh'} <= files.keys():
        raise ValueError('Repository source is incomplete.')
    if sum(map(len, files.values())) > 128 * 1024 * 1024:
        raise ValueError('Source archive exceeds 128 MiB; exclude generated evidence first.')
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    canonical = json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()
    revision = 'uncommitted-or-unavailable'
    dirty = None
    try:
        revision = checked(['git', '-C', str(repository), 'rev-parse', 'HEAD'], capture=True).stdout.decode().strip()
        dirty = bool(checked(['git', '-C', str(repository), 'status', '--porcelain'], capture=True).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    manifest = {'schema_version': 1, 'revision': revision, 'dirty': dirty,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'manifest_sha256': hashlib.sha256(canonical).hexdigest(), 'files': hashes}
    files['SOURCE_MANIFEST.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    files['SOURCE_REVISION'] = (revision + '\n').encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, content in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(content))
    return buffer.getvalue(), manifest


def verify_lab():
    if DISTRO not in distros():
        raise ValueError(DISTRO + ' is absent; use create first.')
    # This tag is written only into a newly imported distro, before installing code.
    raw = wsl('/bin/cat', '/etc/lsrp-wsl-owned', capture=True).stdout.decode().strip()
    if raw != OWNER_TAG:
        raise ValueError('The existing named distro is not owned by this helper; refusing to modify it.')
    raw = wsl('/bin/cat', '/etc/wsl.conf', capture=True).stdout.decode()
    import configparser
    config = configparser.ConfigParser()
    config.read_string(raw)
    required = {'boot': {'systemd': 'true'},
                'automount': {'enabled': 'false', 'mountfstab': 'false'},
                'interop': {'enabled': 'false', 'appendwindowspath': 'false'}}
    for section, settings in required.items():
        for key, value in settings.items():
            if config.get(section, key, fallback='').lower() != value:
                raise ValueError('WSL lab isolation configuration changed: ' + section + '.' + key)


def sync_source(repository, runtime, *, install=False):
    verify_lab()
    payload, manifest = source_archive(repository)
    stage = '/root/lsrp-source-incoming-' + uuid.uuid4().hex
    wsl('/bin/mkdir', '-m', '700', stage)
    wsl('/bin/tar', '-xf', '-', '-C', stage, input=payload)
    script = '''import pathlib, sys, uuid
stage = pathlib.Path(sys.argv[1])
target = pathlib.Path('/root/lsrp-source')
if stage.parent != pathlib.Path('/root') or not stage.name.startswith('lsrp-source-incoming-') or stage.is_symlink():
    raise SystemExit('Unsafe source staging directory')
if target.is_symlink():
    raise SystemExit('Refusing symlink source target')
if target.exists():
    target.rename(pathlib.Path('/root/lsrp-source-backup-' + uuid.uuid4().hex))
stage.rename(target)
'''
    wsl('/usr/bin/python3', '-c', script, stage)
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / 'last-source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if install:
        wsl('/bin/bash', SOURCE + '/scripts/install-lab.sh', '--accept-disposable-lab')
    print('Synced source manifest ' + manifest['manifest_sha256'])


def create_lab(repository, runtime):
    if DISTRO in distros():
        raise ValueError(DISTRO + ' already exists. Creation never overwrites a distro; use sync for a helper-owned lab.')
    install = runtime / 'distro'
    if install.exists():
        raise ValueError('WSL installation directory already exists; refusing to overwrite it: ' + str(install))
    image = download_image(runtime)
    checked(['wsl.exe', '--import', DISTRO, str(install), str(image), '--version', '2'], timeout=300)
    wsl('/bin/sh', '-c', 'cat > /etc/wsl.conf', input=WSL_CONFIG.encode())
    wsl('/bin/sh', '-c', "printf '%s\\n' '" + OWNER_TAG + "' > /etc/lsrp-wsl-owned")
    # Only the newly created named distribution is restarted; global WSL settings stay untouched.
    checked(['wsl.exe', '--terminate', DISTRO])
    wsl('/bin/true')
    wsl('/usr/bin/env', 'DEBIAN_FRONTEND=noninteractive', '/usr/bin/apt-get', 'update', timeout=600)
    wsl('/usr/bin/env', 'DEBIAN_FRONTEND=noninteractive', '/usr/bin/apt-get', 'install', '-y',
        'python3', 'systemd', 'iproute2', timeout=600)
    sync_source(repository, runtime, install=True)
    print('Created and installed the disposable ' + DISTRO + ' lab.')


def run_name(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', value) or '..' in value:
        raise ValueError('Run name must be 1-64 letters, digits, underscore, dot or dash, without consecutive dots.')
    return value


def validate(runtime, name):
    verify_lab()
    name = run_name(name)
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / 'last-validation.json').write_text(json.dumps({'run': name}) + '\n', encoding='utf-8')
    print('Recording validation in ' + RESULTS + '/' + name, flush=True)
    wsl('/bin/bash', SOURCE + '/scripts/validate-linux.sh', RESULTS + '/' + name, timeout=1200)
    print('Fetch with: python scripts/wsl_lab.py fetch --run ' + name)


def extract_results(payload, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Refusing to overwrite a fetched run: ' + str(destination))
    members = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk():
                raise ValueError('Unsafe archive member: ' + member.name)
            if member.isdir():
                if path.as_posix() not in {'.', 'trials'}:
                    raise ValueError('Unexpected archive directory: ' + member.name)
                continue
            name = path.as_posix()
            expected = name in FETCH_FILES or (len(path.parts) == 2 and path.parts[0] == 'trials'
                                              and path.suffix == '.json')
            total += member.size
            if not expected or not member.isfile() or member.size > 64 * 1024 * 1024 or total > 256 * 1024 * 1024:
                raise ValueError('Unexpected or oversized archive member: ' + member.name)
            members.append((member, path))
        destination.mkdir(parents=True)
        for member, path in members:
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('xb') as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)


def fetch(runtime, name):
    verify_lab()
    if name is None:
        name = json.loads((runtime / 'last-validation.json').read_text(encoding='utf-8'))['run']
    name = run_name(name)
    payload = wsl('/bin/tar', '-cf', '-', '-C', RESULTS + '/' + name, '.', capture=True).stdout
    if (runtime / 'results').is_symlink():
        raise ValueError('Refusing symlink results directory.')
    destination = runtime / 'results' / name
    extract_results(payload, destination)
    print('Fetched real Linux evidence to ' + str(destination))


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument('--runtime-dir', type=Path, default=Path.home() / 'lsrp-lab-runtime',
                      help='Directory under your home and outside the repository (default: %(default)s)')
    commands = root.add_subparsers(dest='command', required=True)
    commands.add_parser('create', help='Download/check pinned image, import a new fixed-name distro, configure and install')
    sync = commands.add_parser('sync', help='Explicitly update source in the existing helper-owned lab')
    sync.add_argument('--install', action='store_true', help='Also refresh the root-owned installed service/CLI')
    validation = commands.add_parser('validate', help='Run real Linux validation in the synced lab')
    validation.add_argument('--run', default='validation-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    fetching = commands.add_parser('fetch', help='Fetch only expected evidence from a recorded run')
    fetching.add_argument('--run', help='Run name (default: most recently requested validation)')
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if os.name != 'nt':
            raise ValueError('This host helper requires Windows with WSL2. Use the Linux CLI inside the lab.')
        repository = Path(__file__).resolve().parents[1]
        runtime = runtime_directory(args.runtime_dir, repository)
        if args.command == 'create':
            create_lab(repository, runtime)
        elif args.command == 'sync':
            sync_source(repository, runtime, install=args.install)
        elif args.command == 'validate':
            validate(runtime, args.run)
        elif args.command == 'fetch':
            fetch(runtime, args.run)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print('wsl-lab: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
