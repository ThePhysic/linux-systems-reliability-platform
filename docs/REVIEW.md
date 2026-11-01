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

Pending independent rereview of the fixes and refreshed Linux evidence.
