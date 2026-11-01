# Portfolio Value, Scope and Possible Extensions

## The expertise this should prove

This project should make it credible to say: **I can operate and debug a Linux service, collect relevant process/service/network evidence, design bounded reliability experiments, and quantify whether recovery actually occurred.** The desired demonstration is direct technical competence, not a tool list on a résumé.

## Target-role overlap

| Job family / examples | What this project should demonstrate |
| --- | --- |
| Linux engineering (Jane Street Linux) | systemd, `/proc`, cgroups, process lifecycle, Linux diagnostics, operational ownership |
| Systems engineering (Jump Campus Systems, BlackRock Systems Research) | automation, incident response, resource reasoning, reliability testing, reproducible troubleshooting |
| Cloud/platform infrastructure (Microsoft Azure, NetApp) | service supervision, observability, resilience, failure isolation, recoverability |
| Networking engineering (Apple, Tailscale, Fortinet) | socket diagnosis, TCP/IP behavior, network fault localization, Linux networking tools |
| Systems-oriented SWE (HRT, Jane Street SWE, Quadeye) | sound OS/concurrency/resource fundamentals; additional depth should come from separate C++ systems work |

The project is evidence of learning and engineering skill; it does not replace professional production experience or guarantee suitability for any particular role.

## Resume-worthy outcomes (only after measurement)

Potential statements to support with genuine data:

- Built a Linux-native reliability lab supervising a service under systemd and correlating process, resource, socket and journal signals.
- Designed reproducible crash, hang, CPU-pressure and bind-conflict experiments with verifiable recovery and isolated cleanup.
- Measured detection/recovery distributions across repeated incident trials and documented diagnosis uncertainty.

Replace these with precise measured results when available; do not fabricate numerical improvements.

## Sensible scope boundary

**Core concept:** one supervised service, one observation/incident mechanism, a well-executed set of failure modes, and trustworthy validation.

**Optional expansions:** richer cgroup fault diagnosis, memory/FD exhaustion, network namespace isolation, syscall tracing, eBPF observability, multi-service dependency failures, or a minimal visualization. Optional expansions should improve Linux understanding and not bury it under unrelated application features.

**Intentionally separate:** low-latency C++ event loops, trading/order-book systems, lock-free queue benchmarking, and complex multithreaded performance engineering. Those belong to the high-performance systems project and can complement this project without being required here.

## Decisions deliberately left open

Implementation language, repository layout, Linux distribution, exact CLI syntax, event serialization, tooling libraries, number of test cases, and build workflow are design choices—not requirements implied by this idea package. The project description identifies the **outcomes and constraints** that any credible implementation must satisfy.

## Demonstration narrative

A compelling demonstration follows a question: **"My Linux service is failing—what can I prove is happening, and how do I know it recovered?"** Show baseline operation, one real failure, the corresponding OS signals, an evidence-backed explanation, restoration of end-to-end service, and a concise comparison of repeated runs. Explain one false lead or ambiguous signal to demonstrate systems reasoning.
