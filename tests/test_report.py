import copy
import hashlib
import json
from pathlib import Path
import unittest

from lsrp.report import analyze, render


def envelope(data):
    return {"status": "ok", "source": "test fixture", "data": data}


def sample(stamp, *, ok=True, mode="normal", pid=10, latency=5, throttled=0, active="active", journal=None):
    identity = {"pid": pid, "start_ticks": pid * 100, "boot_id": "test-boot", "mode": mode}
    return {"monotonic": stamp, "completed_monotonic": stamp + 0.01,
            "probe": {"ok": ok, "identity": identity if ok else None, "latency_ms": latency,
                      "completed_monotonic": stamp + 0.01, "error": None if ok else "Request timed out"},
            "process": envelope(identity), "unit": envelope({"ActiveState": active, "NRestarts": "0"}),
            "socket": envelope({"listeners": []}), "journal": envelope({"entries": journal or []}),
            "cgroup": envelope({"path": "/test", "files": {"cpu.stat": {"status": "ok", "value": {
                "usage_usec": int(stamp * 100), "nr_throttled": throttled, "throttled_usec": throttled * 100}}}})}


def trial(samples):
    return {"scenario": "hang", "trial_id": "test", "baseline": [sample(1), sample(2), sample(3)],
            "samples": samples, "injection": {"monotonic": 4, "confirmed": True}, "outcome": "recovered"}


class ReportTests(unittest.TestCase):
    def test_three_successes_verify_recovery_and_timings(self):
        record = trial([sample(5, ok=False), sample(6), sample(7), sample(8)])
        result = analyze(record)
        self.assertTrue(result["recovery"]["sustained"])
        self.assertAlmostEqual(result["metrics"]["detection_latency_s"], 1.01)
        self.assertAlmostEqual(result["metrics"]["recovery_time_s"], 2.01)
        self.assertAlmostEqual(result["metrics"]["recovery_confirmation_time_s"], 4.01)

    def test_live_but_unhealthy_does_not_prove_crash(self):
        result = analyze(trial([sample(5, ok=False)]))
        self.assertTrue(result["functional_failure_observed"])
        self.assertTrue(result["diagnosis"]["hypotheses"])
        self.assertFalse(any("crash" in item["mechanism"].lower() for item in result["diagnosis"]["confirmed_mechanisms"]))

    def test_injection_labels_cannot_change_diagnosis(self):
        original = trial([sample(5, ok=False), sample(6), sample(7), sample(8)])
        altered = copy.deepcopy(original)
        altered["scenario"] = "cpu"
        altered["injection"].update(action="the cause is magically known", details={"signal": "SIGKILL"})
        self.assertEqual(analyze(original)["diagnosis"], analyze(altered)["diagnosis"])

    def test_only_two_successes_or_interleaved_failures_do_not_recover(self):
        result = analyze(trial([sample(5, ok=False), sample(6), sample(7), sample(8, ok=False), sample(9), sample(10)]))
        self.assertFalse(result["recovery"]["sustained"])
        self.assertIsNone(result["metrics"]["recovery_time_s"])

    def test_cpu_mode_does_not_count_as_recovery(self):
        result = analyze(trial([sample(5, mode="cpu", throttled=1), sample(6, mode="cpu", throttled=2),
                               sample(7, mode="cpu", throttled=3), sample(8, mode="cpu", throttled=4)]))
        self.assertTrue(result["throttling_observed"])
        self.assertFalse(result["recovery"]["sustained"])
        self.assertEqual(result["metrics"]["cpu_counter_delta"]["nr_throttled"], 4)

    def test_resource_reset_is_not_negative_or_invented_throttling(self):
        record = trial([sample(5, throttled=1)])
        record["baseline"][-1] = sample(3, throttled=100)
        result = analyze(record)
        self.assertFalse(result["throttling_observed"])
        self.assertTrue(any("resets" in text for text in result["limitations"]))

    def test_new_cgroup_at_same_path_with_larger_counters_is_not_a_delta(self):
        record = trial([sample(5, throttled=100)])
        for item in record["baseline"]:
            item["cgroup"]["data"]["generation"] = {"device": 1, "inode": 100}
        record["samples"][0]["cgroup"]["data"]["generation"] = {"device": 1, "inode": 200}
        result = analyze(record)
        self.assertFalse(result["throttling_observed"])
        self.assertIsNone(result["metrics"]["cpu_counter_delta"])

    def test_process_restart_prevents_deltas_when_generation_metadata_is_missing(self):
        result = analyze(trial([sample(5, pid=11, throttled=100)]))
        self.assertFalse(result["throttling_observed"])
        self.assertIsNone(result["metrics"]["cpu_counter_delta"])

    def test_new_systemd_invocation_prevents_cross_activation_deltas(self):
        record = trial([sample(5, throttled=100)])
        for item in record["baseline"]:
            item["unit"]["data"]["InvocationID"] = "old-invocation"
        record["samples"][0]["unit"]["data"]["InvocationID"] = "new-invocation"
        result = analyze(record)
        self.assertFalse(result["throttling_observed"])

    def test_previous_trial_journal_is_not_current_evidence(self):
        stale = {"__MONOTONIC_TIMESTAMP": "1000000", "MESSAGE": "Address already in use"}
        result = analyze(trial([sample(5, ok=False, journal=[stale])]))
        self.assertFalse(any("bind failure" in item["mechanism"] for item in result["diagnosis"]["confirmed_mechanisms"]))

    def test_missing_baseline_blocks_timely_recovery(self):
        record = trial([sample(5, ok=False), sample(6), sample(7), sample(8)])
        record["baseline"] = []
        result = analyze(record)
        self.assertFalse(result["recovery"]["sustained"])
        self.assertIsNone(result["metrics"]["baseline_latency_ms"]["median"])

    def test_process_restarts_break_success_streak(self):
        result = analyze(trial([sample(5, ok=False), sample(6), sample(7, pid=11), sample(8, pid=11)]))
        self.assertFalse(result["recovery"]["sustained"])

    def test_later_relapse_invalidates_earlier_three_successes(self):
        result = analyze(trial([sample(5, ok=False), sample(6), sample(7), sample(8), sample(9, ok=False)]))
        self.assertFalse(result["recovery"]["sustained"])
        self.assertIsNone(result["metrics"]["recovery_time_s"])
        self.assertEqual(len(result["recovery"]["relapses"]), 1)
        self.assertEqual(len(result["recovery"]["provisional_segments"]), 1)

    def test_final_stable_segment_after_relapse_sets_recovery_time(self):
        result = analyze(trial([sample(5, ok=False), sample(6), sample(7), sample(8), sample(9, ok=False),
                               sample(10), sample(11), sample(12)]))
        self.assertTrue(result["recovery"]["sustained"])
        self.assertAlmostEqual(result["metrics"]["recovery_time_s"], 6.01)
        self.assertAlmostEqual(result["recovery"]["confirmed_monotonic"], 12.01)
        self.assertEqual(len(result["recovery"]["relapses"]), 1)

    def test_identity_switch_after_provisional_recovery_requires_new_three(self):
        early = [sample(5, ok=False), sample(6), sample(7), sample(8)]
        unstable = analyze(trial(early + [sample(9, pid=11), sample(10, pid=11)]))
        self.assertFalse(unstable["recovery"]["sustained"])
        stable = analyze(trial(early + [sample(9, pid=11), sample(10, pid=11), sample(11, pid=11)]))
        self.assertTrue(stable["recovery"]["sustained"])
        self.assertAlmostEqual(stable["metrics"]["recovery_time_s"], 5.01)

    def test_no_sampled_incident_remains_inconclusive(self):
        result = analyze(trial([sample(5), sample(6), sample(7), sample(8)]))
        self.assertFalse(result["incident_observed"])
        self.assertFalse(result["recovery"]["sustained"])
        self.assertIsNone(result["metrics"]["detection_latency_s"])

    def test_render_separates_ground_truth_from_evidence(self):
        text = render(trial([sample(5, ok=False)]))
        self.assertIn("Known experiment actions (ground truth, separate from diagnosis)", text)
        self.assertIn("Limitations:", text)

    def test_render_shows_timeline_and_errors_with_compact_cleanup(self):
        record = trial([sample(5, ok=False)])
        record["error"] = "Experiment interrupted"
        record["events"] = [{"wall_time": "2026-11-01T00:00:00+00:00", "monotonic": 4.25,
                             "phase": "inject", "message": "Fault dispatch acknowledged"}]
        record["cleanup"] = {"ok": False, "errors": ["Helper remained active"],
                             "verification": {"lsrp-collision.service": "active"},
                             "functional_checks": [{"ok": True, "raw": "DO_NOT_DUMP_RAW_CHECK"},
                                                   {"ok": False, "error": "Request timed out"}, {"ok": True}]}
        text = render(record)
        self.assertIn("Run error: Experiment interrupted", text)
        self.assertIn("2026-11-01T00:00:00+00:00 [monotonic 4.250000, t+0.250s] inject: Fault dispatch acknowledged", text)
        self.assertIn("Cleanup functional probe attempts: 2/3 successful (1 failed attempt)", text)
        self.assertIn("Cleanup final recorded probe streak: 1 consecutive successful probe (fewer than the required 3)", text)
        self.assertIn("Cleanup unit verification: lsrp-collision.service = active", text)
        self.assertIn("Cleanup error: Helper remained active", text)
        self.assertIn("Cleanup functional error (1 attempt): Request timed out", text)
        self.assertNotIn("DO_NOT_DUMP_RAW_CHECK", text)
        self.assertIn("injection dispatch; exact failure onset unknown", text)

    def test_counted_symptoms_use_singular_and_plural_nouns(self):
        single = render(trial([sample(5, ok=False), sample(6, latency=100)]))
        self.assertIn("1 functional work probe failed.", single)
        self.assertIn("1 successful request exceeded", single)
        self.assertNotIn("1 successful requests", single)
        multiple = render(trial([sample(5, ok=False), sample(6, ok=False),
                                 sample(7, latency=100), sample(8, latency=100)]))
        self.assertIn("2 functional work probes failed.", multiple)
        self.assertIn("2 successful requests exceeded", multiple)

    def test_relapse_symptoms_use_singular_and_plural_nouns(self):
        early = [sample(5, ok=False), sample(6), sample(7), sample(8), sample(9, ok=False)]
        single = render(trial(early))
        self.assertIn("1 later relapse or process identity change invalidated", single)
        multiple = render(trial(early + [sample(10), sample(11), sample(12), sample(13, ok=False)]))
        self.assertIn("2 later relapses or process identity changes invalidated", multiple)
        self.assertNotIn("relapse(s)", single + multiple)

    def test_render_merges_only_known_caveat_aliases_preserving_all_uncertainties(self):
        record = trial([sample(5, ok=False)])
        record["limitations"] = [
            "Snapshots are sequential, not atomic; collection/probe overhead changes sampling resolution.",
            "Injection dispatch is an experiment timing reference, not exact failure onset.",
            "Monitoring polls can perturb request latency.",
            "Snapshots may omit a short-lived process between samples.",
            "Clock resolution limits the precision of event ordering.",
        ]
        before = copy.deepcopy(record)
        analysis = analyze(record)
        text = render(record)
        limitations = text.split("\nLimitations:\n", 1)[1]
        self.assertEqual(limitations.count("Snapshots are sequential, not atomic;"), 1)
        self.assertNotIn("Serial collection and HTTP timeouts", limitations)
        self.assertIn("collection/probe overhead changes sampling resolution", limitations)
        self.assertIn("HTTP timeouts introduce additional sampling uncertainty", limitations)
        self.assertEqual(limitations.count("Injection dispatch is an experiment timing reference"), 1)
        self.assertIn("not proof of exact failure onset", limitations)
        for caveat in record["limitations"][2:]:
            self.assertIn(caveat, limitations)
        self.assertIn("not long-term availability", limitations)
        self.assertEqual(record, before)
        # Consolidation is presentation-only; neither the stored caveats nor
        # the complete machine-readable analysis are replaced by the summary.
        self.assertEqual(analyze(record), analysis)
        self.assertIn(record["limitations"][0], analysis["limitations"])
        self.assertIn("Serial collection and HTTP timeouts introduce sampling uncertainty; snapshots are not atomic.",
                      analysis["limitations"])

    def test_cleanup_distinguishes_retry_attempts_from_the_recorded_verified_result(self):
        record = trial([sample(5, ok=False)])
        failed = {"ok": False, "error": "[Errno 111] Connection refused"}
        record["cleanup"] = {"ok": True, "errors": [], "verification": {"helper": "inactive"},
                             "functional_checks": [failed, failed, {"ok": True}, {"ok": True}, {"ok": True}]}
        before = copy.deepcopy(record)
        text = render(record)
        self.assertIn("Cleanup: verified (recorded result)", text)
        self.assertIn("Cleanup functional probe attempts: 3/5 successful (2 failed attempts)", text)
        self.assertIn("Cleanup final recorded probe streak: 3 consecutive successful probes", text)
        self.assertIn("2 earlier failed attempts preceded the final recorded successes", text)
        self.assertIn("Cleanup retry error (2 attempts): [Errno 111] Connection refused", text)
        self.assertEqual(record, before)

    def test_successful_probe_attempts_never_override_a_failed_cleanup_verdict(self):
        record = trial([sample(5, ok=False)])
        record["cleanup"] = {"ok": False, "errors": ["Helper remained active"],
                             "functional_checks": [{"ok": True}, {"ok": True}, {"ok": True}]}
        text = render(record)
        self.assertIn("Cleanup: incomplete or unverified (recorded result)", text)
        self.assertIn("Cleanup final recorded probe streak: 3 consecutive successful probes", text)
        self.assertIn("Cleanup error: Helper remained active", text)
        self.assertNotIn("Cleanup: verified", text)

    def test_insufficient_final_streak_does_not_relabel_failures_as_completed_retries(self):
        record = trial([sample(5, ok=False)])
        record["cleanup"] = {"ok": True, "errors": [], "functional_checks": [
            {"ok": True}, {"ok": True}, {"ok": False, "error": "Request timed out"}]}
        text = render(record)
        self.assertIn("Cleanup: verified (recorded result)", text)
        self.assertIn("Cleanup final recorded probe streak: 0 consecutive successful probes (fewer than the required 3)", text)
        self.assertIn("Cleanup functional error (1 attempt): Request timed out", text)
        self.assertNotIn("Cleanup retries:", text)
        self.assertNotIn("Cleanup retry error", text)

    def test_missing_cleanup_probe_sequence_is_explicitly_unavailable(self):
        record = trial([sample(5, ok=False)])
        record["cleanup"] = {"ok": False, "errors": ["not attempted"]}
        text = render(record)
        self.assertIn("Cleanup final recorded probe streak: unavailable", text)
        self.assertIn("Cleanup error: not attempted", text)

    def test_real_hang_and_crash_reports_keep_saved_measurements_and_artifacts_intact(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("hang-325476e5-045c-4fa9-96ef-4d7d4c803f65.json",
                         "crash-697258cf-64b2-4c4f-be39-f1e1e2f56477.json"):
            path = root / "examples" / "robustness" / "validation" / "trials" / filename
            with self.subTest(filename=filename):
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                record = json.loads(raw)
                before = copy.deepcopy(record)
                text = render(record)
                self.assertIn("Outcome: recovered", text)
                self.assertIn("Cleanup functional probe attempts: 3/5 successful (2 failed attempts)", text)
                self.assertIn("Cleanup final recorded probe streak: 3 consecutive successful probes", text)
                self.assertIn("Cleanup retry error (2 attempts): [Errno 111] Connection refused", text)
                self.assertNotIn("1 successful requests", text)
                self.assertEqual(analyze(record)["metrics"], record["metrics"])
                self.assertEqual(analyze(record)["recovery"], record["recovery"])
                self.assertEqual(record, before)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
