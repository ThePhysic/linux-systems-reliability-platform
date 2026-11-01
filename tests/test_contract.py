"""Public CLI contracts that remain meaningful without installing a Linux lab."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid


REPOSITORY = Path(__file__).resolve().parents[1]


class CLIContractTests(unittest.TestCase):
    def invoke(self, *args):
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(REPOSITORY)
        return subprocess.run([sys.executable, '-m', 'lsrp', *map(str, args)],
                              cwd=REPOSITORY, env=environment,
                              text=True, capture_output=True, timeout=20)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn('Traceback', result.stderr)

    def assert_cli_error(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertTrue(result.stderr.strip())
        self.assertNotIn('Traceback', result.stderr)

    def trial(self, scenario='crash', outcome='inconclusive', **metrics):
        return {
            'schema_version': 1, 'trial_id': str(uuid.uuid4()),
            'scenario': scenario, 'outcome': outcome,
            'environment': {'test_fixture': True}, 'parameters': {},
            'baseline': [], 'samples': [], 'events': [],
            'injection': {'confirmed': False, 'details': {}},
            'cleanup': {'ok': True, 'errors': [], 'verification': {}},
            'metrics': metrics, 'limitations': ['Synthetic test record; no real Linux run.'],
        }

    def test_help_exposes_operator_workflow_without_requiring_lab(self):
        result = self.invoke('--help')
        self.assert_success(result)
        for command in ('doctor', 'status', 'start', 'stop', 'run', 'suite',
                        'report', 'compare', 'overhead', 'cleanup'):
            self.assertIn(command, result.stdout)
        self.assertNotIn('--pid', result.stdout)
        self.assertNotIn('--host', result.stdout)

    def test_fault_interface_rejects_arbitrary_target_or_scenario(self):
        self.assert_cli_error(self.invoke('run', 'kill-arbitrary-process', '--output', 'unused.json'))
        self.assert_cli_error(self.invoke('run', 'crash', '--pid', '1', '--output', 'unused.json'))

    def test_offline_json_report_preserves_original_record_and_uncertainty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trial.json'
            record = self.trial()
            path.write_text(json.dumps(record), encoding='utf-8')
            result = self.invoke('report', path, '--json')
            self.assert_success(result)
            self.assertEqual(json.loads(result.stdout), record)
            self.assertEqual(json.loads(path.read_text()), record)
            readable = self.invoke('report', path)
            self.assert_success(readable)
            self.assertIn('Insufficient evidence', readable.stdout)
            self.assertIn('inconclusive', readable.stdout)
            self.assertIn('ground truth', readable.stdout)

    def test_comparison_keeps_failed_trials_and_missing_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trials = [self.trial(outcome='recovered', recovery_time_s=4),
                      self.trial(outcome='failed', recovery_time_s=None),
                      self.trial(outcome='inconclusive', recovery_time_s=None)]
            for index, trial in enumerate(trials):
                (root / f'{index}.json').write_text(json.dumps(trial), encoding='utf-8')
            # A suite's summary is not another trial and must not be included.
            (root / 'summary.json').write_text('{}', encoding='utf-8')
            result = self.invoke('compare', root, '--json')
            self.assert_success(result)
            summary = json.loads(result.stdout)
            self.assertEqual(summary['total_trials'], 3)
            crash = summary['scenarios']['crash']
            self.assertEqual(crash['n'], 3)
            self.assertEqual(crash['outcomes']['recovered'], 1)
            self.assertEqual(crash['outcomes']['failed'], 1)
            self.assertEqual(crash['outcomes']['inconclusive'], 1)
            timing = crash['metrics']['recovery_time_s']
            self.assertEqual(timing['n'], 1)
            self.assertEqual(timing['missing'], 2)
            self.assertEqual(timing['median'], 4)
            self.assertIsNone(timing['p95'])

    def test_duplicate_trial_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trial.json'
            path.write_text(json.dumps(self.trial()), encoding='utf-8')
            result = self.invoke('compare', path, path, '--json')
            self.assert_cli_error(result)
            self.assertIn('Duplicate trial ID', result.stderr)

    def test_bad_or_absent_offline_records_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assert_cli_error(self.invoke('compare', root, '--json'))
            self.assert_cli_error(self.invoke('report', root / 'missing.json'))
            path = root / 'bad.json'
            for payload in ('not json', '[]', '{"schema_version": 2}', '{"schema_version": 1}'):
                path.write_text(payload, encoding='utf-8')
                self.assert_cli_error(self.invoke('report', path))


if __name__ == '__main__':
    unittest.main()
