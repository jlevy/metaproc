---
type: is
id: is-01m0z03c1r90y3g7dpgerahhcq
title: Make bounded-shutdown regression scheduler-independent
kind: bug
status: closed
priority: 1
version: 4
labels:
  - runpool
dependencies: []
created_at: 2026-08-26T12:18:13.176Z
updated_at: 2026-08-26T12:25:39.155Z
closed_at: 2026-08-26T12:25:39.154Z
close_reason: Synchronized the shutdown regression on backend cleanup entry so parallel scheduler delay cannot race the assertion, while preserving the production cleanup bound and terminal-event check.
resolution: null
duplicate_of: null
---
The wedged-backend shutdown regression asserts a 50 ms internal cleanup bound using an unrelated 250 ms outer wall-clock timeout. Under a loaded parallel test runner the expected cleanup diagnostic is emitted, yet the outer guard can expire before the shutdown task is rescheduled. Synchronize the test on entry into the contractual bounded wait, assert the configured cleanup bound directly, and retain a generous deadlock guard without weakening production behavior.

## Notes

The regression now synchronizes on the wedged backend entering kill cleanup before starting its outer deadlock guard. Production still uses the injected 50 ms cleanup bound and must emit the terminal pool_shutdown event; the one-second outer guard is now only a post-synchronization deadlock failsafe, not a timing assertion competing with parallel-worker scheduling. Focused serial and xdist tests pass.
