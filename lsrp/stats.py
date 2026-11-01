"""Small sample summaries which preserve missingness and avoid misleading tails."""
from __future__ import annotations

import math
from statistics import median


def distribution(values):
    original = list(values)
    numbers = sorted(float(v) for v in original
                     if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))
    if not numbers:
        return {"n": 0, "missing": len(original), "median": None, "min": None, "max": None, "p95": None}
    return {"n": len(numbers), "missing": len(original) - len(numbers), "median": median(numbers),
            "min": numbers[0], "max": numbers[-1],
            "p95": numbers[math.ceil(0.95 * len(numbers)) - 1] if len(numbers) >= 20 else None}


def summarize(trials):
    groups = {}
    for trial in trials:
        groups.setdefault(trial.get("scenario", "unknown"), []).append(trial)
    result = {"schema_version": 1, "total_trials": sum(map(len, groups.values())), "scenarios": {},
              "notes": ["p95 uses nearest rank and requires at least 20 available measurements.",
                        "Failed and inconclusive trials remain in outcome counts; unavailable timings are not zero."]}
    for name, records in sorted(groups.items()):
        group = {"n": len(records), "outcomes": {key: sum(r.get("outcome") == key for r in records)
                                                 for key in ("recovered", "failed", "inconclusive")}, "metrics": {}}
        group["outcomes"]["unknown"] = len(records) - sum(group["outcomes"].values())
        keys = {"detection_latency_s", "recovery_time_s", "recovery_from_first_observed_symptom_s", "systemd_restart_delay_s"}
        keys |= {key for record in records for key, value in record.get("metrics", {}).items()
                 if isinstance(value, (float, int)) and not isinstance(value, bool)}
        for key in sorted(keys):
            group["metrics"][key] = distribution(record.get("metrics", {}).get(key) for record in records)
        result["scenarios"][name] = group
    return result
