# Follow-up robustness verification

This is a fresh real-Linux validation of the malformed-record and probe-deadline fixes. The original reference run remains under `examples/validation`; this directory preserves every trial from the follow-up run rather than replacing historical evidence.

The exact validated source is revision `e49d359`, recorded with file hashes in [SOURCE_MANIFEST.json](validation/SOURCE_MANIFEST.json). Implementation commits are `1d34973` (saved-record validation) and `06c60b4` (absolute probe deadline). The following commit includes archived incident fixtures in Linux source transfers so compatibility checks execute in the lab.

- [Full validation](validation/validation-summary.json): 100 unit tests, graceful lifecycle, 12/12 recovered incident trials, three paired observation-overhead measurements, and verified cleanup.
- [Interruption validation](interruption/validation.json): eight checks passed, including SIGTERM cleanup, exclusive-operation rejection, SIGKILL checkpoint preservation, and independent timer-driven service restoration.
- [Unprivileged Linux test log](nonroot-unit-tests.txt): all 100 tests passed as `nobody`.
- [Raw measurements](validation/comparison.json): injection-relative timing distributions for this source and environment. Three trials per incident remain descriptive rather than stable tail estimates.

Deadline regressions use real local sockets for slow status lines, headers, fixed-length bodies, chunked bodies and trailers; all consume the same total budget. Complete responses, redirect rejection, identity verification and oversized/invalid bodies are checked separately. A late result is never accepted as healthy.

Saved-input regressions exercise the original null timeline entry through real CLI processes and malformed nested structures through every report/compare mode. Explicitly unavailable observations and incomplete checkpoints remain readable without changing the original data.

The direct deadline-aware transport changes client overhead compared with the older reference run. Its timings must be interpreted with this revision and source manifest; combining the two runs into one benchmark would obscure that change.
