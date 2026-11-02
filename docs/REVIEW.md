# Independent adversarial review

Reviewer: **gpt-6-astra, medium reasoning**, independently delegated after implementation and initial real Linux validation. Review date: 2026-11-01. Publication is gated on an explicit clean rereview.

## Round 1 findings and fixes

| Finding | Severity | Resolution and regression evidence |
| --- | --- | --- |
| Volatile `/run/lsrp` absent after reboot | P1 | Mutation lock verifies `/run`, safely recreates the fixed owned directory, then validates ownership. Actual dedicated WSL termination/restart followed by CLI start/status passed without reinstall. Lock tests exercise absent runtime directory and actual flock using temporary paths. |
| Missing supervision evidence could pass a response | P2 | Snapshot success now requires an active unit, positive MainPID and affirmative matching process/kernel/response identity. Tests cover absent, unavailable and inactive supervision. |
| Earlier recovery survived a later relapse | P2 | Analysis evaluates every sample and credits only the final stable segment; relapses invalidate provisional recovery. Tests cover failure after three successes, renewed recovery, and process-identity changes. |
| Hung requests exhausted all liveness handlers | P2 | Eight hung request waiters share twelve bounded handler slots; excess fault requests receive 503. A concurrent twelve-client regression verifies `/live` and drained shutdown. |
| Redirects escaped fixed probe destination | P2 | HTTP redirects are rejected. A real two-server loopback regression proves the second destination receives no requests. |

The reviewer independently audited the initial twelve saved trials and found their measured recovery and process identities supported. The review found no fabricated measurements or unsupported performance claims.

## Final verdict

Independent adversarial rereview of commit `5c22e95022207010d197b005e3bf821c84b111e0`: **CLEAN: no remaining complaints within the agreed scope.** All five initial findings are resolved with regression coverage and refreshed Linux evidence. Runtime source matches the clean validated revision `da56e81`. No further implementation changes are requested.

The reviewer verified 83 Windows tests with two expected Linux-only skips, matching validated source hashes, all twelve refreshed Linux trial metrics and recovery claims, eight interruption checks, three overhead pairs, and actual restart-without-reinstall evidence. The same 83 tests also passed as an unprivileged Linux user.

## Follow-up robustness fixes

A subsequent audit identified two additional edge cases: malformed nested saved-record fields could reach the renderer, and per-read HTTP timeouts could be extended by slow response progress.

- `1d34973` validates supplied record structures before every report/compare mode, with field-specific errors and preservation of valid unavailable/incomplete observations.
- `06c60b4` applies one absolute monotonic budget to connect/send/status/headers/body/chunk/trailer reads, closes transport resources on exit, and retains identity and redirect checks.
- `e49d359` includes archived record fixtures in the disposable Linux source transfer so compatibility tests run there as well as on ordinary CI checkouts.

Fresh [verification artifacts](../examples/robustness/README.md) record 100 passing tests, twelve recovered incident trials, three overhead pairs, and eight interruption checks against the corrected implementation.

Independent adversarial follow-up review of commit `4e94c34906c4bf9fedcee94d072c4b0c89edf5e2`: **CLEAN: no remaining complaints within the agreed scope.** Saved-record validation and absolute probe deadlines resolve both reported defects. Regression tests and refreshed Linux evidence support the implementation; no further changes are requested.

The reviewer verified 100 Windows tests with two expected Linux-only skips, additional socket-framing checks, matching source hashes for validated revision `e49d359`, all twelve refreshed Linux trial metrics and recovery claims, 100 Linux tests, 100 unprivileged Linux tests, eight interruption checks, and three overhead pairs.
