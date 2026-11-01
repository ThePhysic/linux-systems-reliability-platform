"""Evidence-only incident interpretation; experiment labels are never diagnoses."""
from __future__ import annotations

import json
import math
from statistics import median

from .stats import distribution


def _data(sample, key):
    envelope = sample.get(key) or {}
    return envelope.get("data") or {} if envelope.get("status") == "ok" else {}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _time(sample):
    probe = sample.get("probe") or {}
    return probe.get("completed_monotonic", sample.get("completed_monotonic", sample.get("monotonic")))


def _cpu(sample):
    group = _data(sample, "cgroup")
    counter = group.get("files", {}).get("cpu.stat", {})
    generation = group.get("generation") or {}
    inode = (generation.get("device"), generation.get("inode")) if generation.get("inode") is not None else None
    invocation = _data(sample, "unit").get("InvocationID") or None
    identity = _identity(sample)
    # A path can be reused by a new service activation with higher counters.
    # Never infer a delta across inode, invocation, or known process changes.
    key = (group.get("path"), inode, invocation, identity) if (inode or invocation or identity) else None
    return (key, counter.get("value")) if counter.get("status") == "ok" else (None, None)


def _identity(sample):
    process = _data(sample, "process")
    if not process:
        process = (sample.get("probe") or {}).get("identity") or {}
    keys = ("boot_id", "pid", "start_ticks")
    return tuple(process.get(k) for k in keys) if all(process.get(k) is not None for k in keys) else None


def _journal_entries(sample, after):
    for entry in _data(sample, "journal").get("entries", []):
        try:
            stamp = int(entry["__MONOTONIC_TIMESTAMP"]) / 1_000_000
        except (KeyError, ValueError, TypeError):
            continue
        if after is None or stamp >= after:
            yield entry


def analyze(trial):
    """Interpret observations without consulting scenario, action or injection details.

    The injection monotonic timestamp is used solely as an explicitly imperfect
    timing reference. It contributes no evidence to causal interpretation.
    """
    baseline = [s for s in trial.get("baseline", []) if isinstance(s, dict)]
    samples = [s for s in trial.get("samples", []) if isinstance(s, dict)]
    baseline.sort(key=lambda s: s.get("monotonic", 0))
    samples.sort(key=lambda s: s.get("monotonic", 0))
    all_samples = baseline + samples
    baseline_latencies = [s["probe"]["latency_ms"] for s in baseline
                          if (s.get("probe") or {}).get("ok") and _number(s["probe"].get("latency_ms"))]
    baseline_median = median(baseline_latencies) if baseline_latencies else None
    latency_threshold = max(50.0, 3 * baseline_median) if baseline_median is not None else None
    reference = (trial.get("injection") or {}).get("monotonic")
    if not _number(reference):
        reference = None
    earliest = min((s["monotonic"] for s in all_samples if _number(s.get("monotonic"))), default=None)
    symptoms, evidence, hypotheses, mechanisms, limitations = [], [], [], [], []
    failures, active_failures, degraded, throttle_samples = [], [], [], []
    cpu_totals = {"nr_throttled": 0, "throttled_usec": 0, "usage_usec": 0}
    cpu_available = False
    previous_cpu = None
    cpu_resets = False
    seen_journal = set()
    journal = []
    identities = []
    restart_counters = []
    for position, sample in enumerate(all_samples):
        is_trial = position >= len(baseline)
        identity = _identity(sample)
        if identity and (not identities or identity != identities[-1]):
            identities.append(identity)
        unit = _data(sample, "unit")
        try:
            restart_counters.append(int(unit["NRestarts"]))
        except (KeyError, TypeError, ValueError):
            pass
        request = sample.get("probe") or {}
        if is_trial and request.get("ok") is False:
            failures.append(sample)
            if unit.get("ActiveState") == "active" and _data(sample, "process"):
                active_failures.append(sample)
        if (is_trial and request.get("ok") and latency_threshold is not None
                and _number(request.get("latency_ms")) and request["latency_ms"] > latency_threshold):
            degraded.append(sample)
        generation, cpu = _cpu(sample)
        if isinstance(cpu, dict):
            if previous_cpu and generation is not None and generation == previous_cpu[0]:
                before = previous_cpu[1]
                available_keys = [k for k in cpu_totals if k in cpu and k in before]
                if any(cpu[k] < before[k] for k in available_keys):
                    cpu_resets = True
                elif is_trial:
                    cpu_available = True
                    for key in available_keys:
                        cpu_totals[key] += cpu[key] - before[key]
                    if "nr_throttled" in available_keys and cpu["nr_throttled"] > before["nr_throttled"]:
                        throttle_samples.append(sample)
            elif previous_cpu and generation != previous_cpu[0]:
                cpu_resets = True
            previous_cpu = (generation, cpu)
        else:
            previous_cpu = None
        for entry in _journal_entries(sample, reference if reference is not None else earliest):
            key = (entry.get("__MONOTONIC_TIMESTAMP"), str(entry.get("MESSAGE")))
            if key not in seen_journal:
                seen_journal.add(key)
                journal.append(entry)
    if failures:
        symptoms.append(f"{len(failures)} functional work probes failed.")
        for sample in failures[:3]:
            p = sample.get("probe") or {}
            evidence.append({"source": p.get("source", "HTTP /work"), "at_monotonic": _time(sample),
                             "observation": p.get("error") or "Functional probe failed"})
    if active_failures:
        symptoms.append("The unit was active and its main process existed during failed work probes.")
        hypotheses.append({"explanation": "A running process was unable to serve valid work; a process crash is not established by these failed probes.",
                           "confidence": "supported", "basis": "unit/process observations correlated with failed requests"})
    if degraded:
        symptoms.append(f"{len(degraded)} successful requests exceeded the baseline-relative latency threshold ({latency_threshold:.2f} ms).")
        hypotheses.append({"explanation": "Request latency degraded; resource contention is one possible explanation.",
                           "confidence": "possible", "basis": "latency alone does not identify a cause"})
    if throttle_samples:
        symptoms.append("The service cgroup accumulated CPU throttling events during the trial.")
        mechanisms.append({"mechanism": "Service-local CPU bandwidth throttling occurred.",
                           "basis": "Positive deltas in the same cgroup's cpu.stat nr_throttled counter",
                           "source": "cgroup v2 cpu.stat", "confidence": "confirmed observation"})
        evidence.append({"source": "cgroup v2 cpu.stat", "observation": cpu_totals,
                         "at_monotonic": _time(throttle_samples[0])})
        limitations.append("Throttling counters establish bandwidth enforcement, not that throttling alone caused every slow request.")
    if len(set(identities)) > 1:
        mechanisms.append({"mechanism": "The observed main process identity changed.",
                           "basis": "boot_id/PID/start_ticks changed across observations", "confidence": "confirmed observation"})
        evidence.append({"source": "/proc identity / validated response identity", "observation": [list(i) for i in identities]})
    if restart_counters and max(restart_counters) > min(restart_counters):
        mechanisms.append({"mechanism": "systemd's automatic restart counter increased.",
                           "basis": "NRestarts delta", "confidence": "confirmed observation"})
    messages = [str(e.get("MESSAGE", "")) for e in journal]
    bind_entries = [m for m in messages if any(term in m.lower() for term in
                    ("address already in use", "errno 98", "eaddrinuse"))]
    if bind_entries:
        mechanisms.append({"mechanism": "The service reported a bind failure because the address was already in use.",
                           "basis": "Service-scoped journal entry", "source": "journald", "confidence": "reported by service"})
        evidence.extend({"source": "journald", "observation": m} for m in bind_entries[:2])
        listeners = [line for sample in samples for line in _data(sample, "socket").get("listeners", [])]
        if listeners:
            evidence.append({"source": "ss", "observation": list(dict.fromkeys(listeners))[:3]})
        else:
            limitations.append("No listener ownership observation corroborated the bind failure.")
    exit_entries = [m for m in messages if ("code=killed" in m.lower() or "code=dumped" in m.lower()
                    or "main process exited" in m.lower() or "failed with result" in m.lower())]
    if exit_entries:
        evidence.extend({"source": "journald", "observation": m} for m in exit_entries[:3])
        mechanisms.append({"mechanism": "systemd recorded a main-process exit or failed service result.",
                           "basis": "Service-scoped journal entries", "source": "journald", "confidence": "confirmed observation"})
    # Recovery requires an observed symptom first and three normal, timely successes.
    incident_candidates = [s for s in failures + degraded + throttle_samples if _number(_time(s))]
    first_symptom = min(incident_candidates, key=_time) if incident_candidates else None
    detection = _time(first_symptom) if first_symptom else None
    streak = []
    recovery_first = recovery_confirmed = None
    if first_symptom:
        for sample in samples:
            request = sample.get("probe") or {}
            identity = request.get("identity") or {}
            timely = (_number(request.get("latency_ms")) and latency_threshold is not None
                      and request["latency_ms"] <= latency_threshold)
            if (_number(_time(sample)) and _time(sample) > detection and request.get("ok")
                    and identity.get("mode") == "normal" and timely):
                # Process identity must remain the same throughout a sustained streak.
                if streak and _identity(streak[-1]) != _identity(sample):
                    streak = []
                streak.append(sample)
                if len(streak) == 3:
                    recovery_first = _time(streak[0])
                    recovery_confirmed = _time(streak[-1])
                    break
            else:
                streak = []
    onset_bounds = None
    if first_symptom:
        previous = [s for s in all_samples if _number(_time(s)) and _time(s) < detection]
        onset_bounds = [max((_time(s) for s in previous), default=reference), detection]
    if baseline_median is None:
        limitations.append("No successful baseline latency exists, so timely recovery cannot be established.")
    if not first_symptom:
        limitations.append("No sampled functional failure, baseline-relative latency degradation or cgroup throttling increment was observed.")
    if cpu_resets:
        limitations.append("Cgroup generation changes or counter decreases were treated as resets; no deltas were inferred across them.")
    unavailable = {}
    for sample in all_samples:
        for key in ("unit", "process", "cgroup", "socket", "journal"):
            env = sample.get(key) or {}
            if env.get("status") != "ok":
                unavailable[key] = unavailable.get(key, 0) + 1
    if unavailable:
        limitations.append("Unavailable observation counts: " + json.dumps(unavailable, sort_keys=True))
    limitations.extend(["Serial collection and HTTP timeouts introduce sampling uncertainty; snapshots are not atomic.",
                        "Injection dispatch is an experiment timing reference, not proof of exact failure onset.",
                        "Three consecutive normal identity-validated work responses establish sampled recovery, not long-term availability."])
    latencies = [s["probe"]["latency_ms"] for s in samples if (s.get("probe") or {}).get("ok")
                 and _number(s["probe"].get("latency_ms"))]
    restart_delay = None
    # Derive restart delay only from paired journal exit and next start events.
    exited_at = None
    for entry in sorted(journal, key=lambda e: int(e.get("__MONOTONIC_TIMESTAMP", 0))):
        message = str(entry.get("MESSAGE", "")).lower()
        stamp = int(entry["__MONOTONIC_TIMESTAMP"]) / 1_000_000
        if "main process exited" in message:
            exited_at = stamp
        elif exited_at is not None and (message.startswith("started ") or message.startswith("starting ")):
            restart_delay = max(0.0, stamp - exited_at)
            break
    metrics = {
        "timing_reference": "confirmed injection dispatch; exact failure onset unknown" if reference is not None else "no injection timestamp",
        "detection_latency_s": max(0.0, detection - reference) if detection is not None and reference is not None else None,
        "recovery_time_s": max(0.0, recovery_first - reference) if recovery_first is not None and reference is not None else None,
        "recovery_confirmation_time_s": max(0.0, recovery_confirmed - reference) if recovery_confirmed is not None and reference is not None else None,
        "recovery_from_first_observed_symptom_s": recovery_first - detection if recovery_first is not None else None,
        "failure_onset_bounds_monotonic": onset_bounds,
        "systemd_restart_delay_s": restart_delay,
        "baseline_latency_ms": distribution(baseline_latencies),
        "trial_success_latency_ms": distribution(latencies),
        "latency_degradation_threshold_ms": latency_threshold,
        "probe_success_ratio": sum(bool((s.get("probe") or {}).get("ok")) for s in samples) / len(samples) if samples else None,
        "probe_count": len(samples), "failed_probe_count": len(failures),
        "cpu_counter_delta": cpu_totals if cpu_available else None,
        "process_identity_count": len(set(identities)),
    }
    return {"diagnosis": {"symptoms": symptoms, "evidence": evidence, "hypotheses": hypotheses,
                          "confirmed_mechanisms": mechanisms, "limitations": limitations},
            "metrics": metrics, "incident_observed": first_symptom is not None,
            "functional_failure_observed": bool(failures), "throttling_observed": bool(throttle_samples),
            "recovery": {"sustained": recovery_confirmed is not None, "first_success_monotonic": recovery_first,
                         "confirmed_monotonic": recovery_confirmed, "consecutive_successes": len(streak)},
            "limitations": list(dict.fromkeys(list(trial.get("limitations", [])) + limitations))}


def render(trial):
    analysis = analyze(trial)
    lines = [f"LSRP incident {trial.get('trial_id', '(unknown)')}",
             f"Experiment: {trial.get('scenario', 'unknown')} | Outcome: {trial.get('outcome', 'not recorded')}", "",
             "Observed symptoms:"]
    diagnosis = analysis["diagnosis"]
    lines += ["  - " + text for text in diagnosis["symptoms"]] or ["  No qualifying symptom was sampled."]
    lines += ["", "Evidence (independent of the experiment label):"]
    lines += ["  - " + str(item.get("source")) + ": " + str(item.get("observation")) for item in diagnosis["evidence"]]
    lines += ["", "Interpretation:"]
    lines += ["  - " + item["mechanism"] + " [" + item["confidence"] + "]" for item in diagnosis["confirmed_mechanisms"]]
    lines += ["  - " + item["explanation"] + " [" + item["confidence"] + "]" for item in diagnosis["hypotheses"]]
    if not diagnosis["confirmed_mechanisms"] and not diagnosis["hypotheses"]:
        lines.append("  Insufficient evidence to identify a failure mechanism.")
    lines += ["", "Known experiment actions (ground truth, separate from diagnosis):",
              "  " + json.dumps(trial.get("injection", {}), sort_keys=True), "", "Recovery verification:",
              "  " + json.dumps(analysis["recovery"], sort_keys=True),
              "  Cleanup: " + json.dumps(trial.get("cleanup", {}), sort_keys=True), "", "Measurements:",
              json.dumps(analysis["metrics"], indent=2, sort_keys=True), "", "Limitations:"]
    lines += ["  - " + text for text in analysis["limitations"]]
    return "\n".join(lines) + "\n"
