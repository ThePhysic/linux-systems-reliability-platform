#!/usr/bin/env bash
# Run only inside an installed disposable Linux lab. Never fabricates evidence.
set -Eeuo pipefail
unset PYTHONOPTIMIZE

if [[ "${EUID}" -ne 0 ]]; then
    echo 'Validation requires root inside the disposable Linux lab.' >&2
    exit 2
fi
if [[ "$#" -gt 1 ]]; then
    echo 'Usage: sudo bash scripts/validate-linux.sh [OUTPUT_DIRECTORY]' >&2
    exit 2
fi

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd -- "$repo_dir"
output_dir="${1:-artifacts/validation}"
mkdir -p -- "$output_dir"
output_dir="$(cd -- "$output_dir" && pwd -P)"
if [[ -n "$(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "Refusing to mix validation evidence with existing files: $output_dir" >&2
    exit 2
fi

cleanup_validation() {
    validation_status=$?
    trap - EXIT INT TERM
    final_cleanup_ok=true
    if ! python3 -m lsrp cleanup >"$output_dir/cleanup.txt" 2>&1; then
        final_cleanup_ok=false
        echo 'Validation cleanup failed; inspect cleanup.txt.' >&2
        if [[ "$validation_status" -eq 0 ]]; then validation_status=1; fi
    fi
    python3 - "$output_dir" "$validation_status" "$final_cleanup_ok" <<'PY'
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
status = int(sys.argv[2])
cleanup_ok = sys.argv[3] == 'true'
result = {'exit_status': status, 'final_cleanup_ok': cleanup_ok, 'passed': status == 0 and cleanup_ok}
(out / 'validation-exit.json').write_text(json.dumps(result, indent=2) + '\n')
summary = out / 'validation-summary.json'
if summary.exists():
    value = json.loads(summary.read_text())
    value.update(final_cleanup_ok=cleanup_ok, passed=value.get('passed', False) and result['passed'])
    summary.write_text(json.dumps(value, indent=2) + '\n')
PY
    if [[ "$validation_status" -eq 0 ]]; then
        echo "Validation passed; evidence written to $output_dir"
    fi
    exit "$validation_status"
}
trap cleanup_validation EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

python3 - "$output_dir" <<'PY'
import json
import pathlib
import runpy
import sys

out = pathlib.Path(sys.argv[1])
helper = runpy.run_path('scripts/wsl_lab.py')
_, actual = helper['source_archive'](pathlib.Path.cwd())
transferred = pathlib.Path('SOURCE_MANIFEST.json')
if transferred.exists():
    recorded = json.loads(transferred.read_text())
    if recorded.get('files') != actual['files'] or recorded.get('manifest_sha256') != actual['manifest_sha256']:
        raise SystemExit('Transferred source manifest does not match the files being validated; sync again.')
    actual = recorded
revision_file = pathlib.Path('SOURCE_REVISION')
if revision_file.exists():
    revision = revision_file.read_text().strip()
    if transferred.exists() and revision != actual['revision']:
        raise SystemExit('SOURCE_REVISION disagrees with the transferred source manifest.')
else:
    revision = actual['revision']
(out / 'SOURCE_MANIFEST.json').write_text(json.dumps(actual, indent=2, sort_keys=True) + '\n')
(out / 'SOURCE_REVISION').write_text(revision + '\n')
PY

{
    date -u '+%Y-%m-%dT%H:%M:%SZ'
    uname -a
    cat /etc/os-release
    python3 --version
    systemctl --version
    printf 'source_revision='
    cat "$output_dir/SOURCE_REVISION"
    printf 'source_manifest_sha256='
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["manifest_sha256"])' "$output_dir/SOURCE_MANIFEST.json"
    printf 'source_dirty='
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["dirty"])' "$output_dir/SOURCE_MANIFEST.json"
} >"$output_dir/environment.txt" 2>&1

python3 -m unittest discover -s tests -v >"$output_dir/unit-tests.txt" 2>&1
python3 -m compileall -q lsrp tests
python3 -m lsrp doctor --json >"$output_dir/doctor.json"
python3 -m lsrp start >"$output_dir/start.txt"
python3 -m lsrp status --json >"$output_dir/lifecycle-before.json"
systemctl show lsrp-lab.service --property=InvocationID --value >"$output_dir/lifecycle-invocation.txt"
python3 -m lsrp stop >"$output_dir/stop.txt"
if python3 -m lsrp status --json >"$output_dir/lifecycle-stopped.json"; then
    echo 'Stopped service unexpectedly passed the work probe.' >&2
    exit 1
else
    stopped_status=$?
    if [[ "$stopped_status" -ne 1 ]]; then
        echo "Stopped-status inspection failed unexpectedly: $stopped_status" >&2
        exit "$stopped_status"
    fi
fi
journalctl -b -u lsrp-lab.service -n 80 --no-pager -o json >"$output_dir/lifecycle-journal.jsonl"
python3 -m lsrp start >"$output_dir/restart.txt"
python3 -m lsrp status --json >"$output_dir/lifecycle-after.json"

python3 - "$output_dir" <<'PY'
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
before = json.loads((out / 'lifecycle-before.json').read_text())
stopped = json.loads((out / 'lifecycle-stopped.json').read_text())
after = json.loads((out / 'lifecycle-after.json').read_text())
assert before['probe']['ok'] is True, 'Service did not serve baseline work'
assert stopped['probe']['ok'] is False, 'Service continued to serve after stop'
assert stopped['unit']['data']['ActiveState'] == 'inactive', 'Unit did not stop'
assert after['probe']['ok'] is True, 'Service did not recover after normal start'
old = before['probe']['identity']
new = after['probe']['identity']
assert (old['pid'], old['start_ticks'], old['boot_id']) != (new['pid'], new['start_ticks'], new['boot_id']), 'Lifecycle reused an old process identity'
invocation = (out / 'lifecycle-invocation.txt').read_text().strip()
entries = [json.loads(line) for line in (out / 'lifecycle-journal.jsonl').read_text().splitlines() if line.strip()]
messages = [e.get('MESSAGE', '') for e in entries if e.get('_SYSTEMD_INVOCATION_ID') == invocation]
assert any('shutdown_complete' in message for message in messages), 'No graceful shutdown-complete event for the stopped invocation'
print('Normal lifecycle and graceful shutdown verified.')
PY

python3 -m lsrp suite --trials 3 --duration 4 --output "$output_dir/trials" >"$output_dir/suite.txt"
python3 -m lsrp compare "$output_dir/trials" --json >"$output_dir/comparison.json"
python3 -m lsrp overhead --trials 3 --duration 4 --output "$output_dir/overhead.json" >"$output_dir/overhead.txt"

python3 - "$output_dir" <<'PY'
import collections
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
records = []
for path in sorted((out / 'trials').rglob('*.json')):
    value = json.loads(path.read_text())
    if isinstance(value, dict) and 'trial_id' in value:
        records.append((path, value))
assert len(records) == 12, f'Expected 12 trial records; got {len(records)}'
assert len({v['trial_id'] for _, v in records}) == 12, 'Duplicate trial IDs'
counts = collections.Counter(v['scenario'] for _, v in records)
assert counts == {'crash': 3, 'hang': 3, 'cpu': 3, 'collision': 3}, counts
required_envelope_fields = {'source', 'collected_at', 'status', 'data', 'error'}
for path, trial in records:
    assert trial['schema_version'] == 1, path
    assert trial['outcome'] == 'recovered', (path, trial['outcome'])
    assert trial['cleanup']['ok'] is True, (path, 'cleanup')
    assert not trial['cleanup']['errors'], (path, trial['cleanup']['errors'])
    assert trial['injection']['confirmed'] is True, (path, 'unconfirmed injection')
    assert trial['baseline'] and all(s['probe']['ok'] for s in trial['baseline']), (path, 'baseline')
    samples = trial['samples']
    assert len(samples) >= 3, (path, 'too few observations')
    assert all(samples[i]['monotonic'] <= samples[i + 1]['monotonic'] for i in range(len(samples) - 1)), (path, 'chronology')
    assert all(s['probe']['ok'] for s in samples[-3:]), (path, 'no sustained final recovery')
    assert all(s['probe']['identity'].get('mode') == 'normal' for s in samples[-3:]), (path, 'fault not cleared')
    for snapshot in trial['baseline'] + samples:
        assert snapshot['wall_time'], (path, 'missing timestamp')
        for family in ('unit', 'process', 'cgroup', 'socket', 'journal'):
            envelope = snapshot[family]
            assert required_envelope_fields <= set(envelope), (path, family)
            assert envelope['status'] in {'ok', 'unavailable', 'error'}, (path, family)
            assert envelope['source'] and envelope['collected_at'], (path, family)
            if envelope['status'] != 'ok':
                assert envelope['error'], (path, family, 'missing explanation')
    assert trial.get('metrics'), (path, 'missing derived metrics')
overhead = json.loads((out / 'overhead.json').read_text())
comparison = json.loads((out / 'comparison.json').read_text())
assert isinstance(overhead, dict) and overhead, 'Empty overhead record'
assert isinstance(comparison, (dict, list)) and comparison, 'Empty comparison'
assert overhead.get('kind') == 'observation_overhead', 'Not an overhead measurement record'
assert overhead.get('n') == 3 and len(overhead.get('trials', [])) == 3, 'Expected three overhead pairs'
for pair in overhead['trials']:
    for mode in ('baseline', 'observed'):
        measurement = pair[mode]
        assert measurement['requests'] > 0 and measurement['successes'] > 0, (mode, 'no successful workload')
        assert measurement['success_ratio'] == 1, (mode, 'functional workload failures')
        assert not measurement['collector_errors'], (mode, measurement['collector_errors'])
    assert pair['baseline']['snapshots'] == 0, 'Baseline unexpectedly collected snapshots'
    assert pair['observed']['snapshots'] > 0, 'Observed workload did not collect snapshots'
assert comparison['total_trials'] == 12, 'Aggregate omitted trials'
assert all(comparison['scenarios'][name]['n'] == 3 for name in counts), 'Aggregate grouping mismatch'
summary = {
    'schema_version': 1,
    'kind': 'linux_validation',
    'passed': True,
    'trial_count': len(records),
    'scenario_counts': dict(sorted(counts.items())),
    'checks': ['portable_tests', 'normal_lifecycle', 'graceful_shutdown', 'confirmed_faults', 'evidence_envelopes', 'monotonic_chronology', 'sustained_work_recovery', 'cleanup', 'repeated_trials', 'overhead_record'],
    'limitations': ['Three trials per incident provide a reproducibility check, not a stable tail estimate.', 'Results apply to the environment and revision recorded in environment.txt.'],
}
(out / 'validation-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
PY

