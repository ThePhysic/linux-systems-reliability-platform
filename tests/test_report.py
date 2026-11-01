import copy
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
        self.assertIn("Cleanup functional checks: 2/3 successful", text)
        self.assertIn("Cleanup unit verification: lsrp-collision.service = active", text)
        self.assertIn("Cleanup error: Helper remained active", text)
        self.assertIn("Cleanup functional error: Request timed out", text)
        self.assertNotIn("DO_NOT_DUMP_RAW_CHECK", text)
        self.assertIn("injection dispatch; exact failure onset unknown", text)


if __name__ == "__main__":
    unittest.main()
