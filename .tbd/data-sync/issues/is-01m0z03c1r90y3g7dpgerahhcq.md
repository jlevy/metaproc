---
type: is
id: is-01m0z03c1r90y3g7dpgerahhcq
title: Make bounded-shutdown regression scheduler-independent
kind: bug
status: closed
priority: 1
version: 6
labels:
  - runpool
dependencies: []
created_at: 2026-08-26T12:18:13.176Z
updated_at: 2026-08-26T12:42:57.510Z
closed_at: 2026-08-26T12:42:57.510Z
close_reason: "Independent precommit review findings fixed: artifact uploads are create-only with generation-conditional retry; both gcp stage and gcp run validate GCP-safe immutable identities before artifact work; maintained architecture/runbook/module/changelog inventories agree; and the bounded-shutdown regression now bounds both synchronization waits. Focused tests, Ruff, BasedPyright, public hygiene, and the full Metaproc verification pass."
resolution: null
duplicate_of: null
---
The wedged-backend shutdown regression asserts a 50 ms internal cleanup bound using an unrelated 250 ms outer wall-clock timeout. Under a loaded parallel test runner the expected cleanup diagnostic is emitted, yet the outer guard can expire before the shutdown task is rescheduled. Synchronize the test on entry into the contractual bounded wait, assert the configured cleanup bound directly, and retain a generous deadlock guard without weakening production behavior.

## Notes

Precommit review found the new kill_started synchronization wait itself is unbounded except for pytest's global 60-second timeout. Wrap that setup wait in a local asyncio wait_for deadlock guard while keeping the production 50 ms cleanup bound and terminal pool_shutdown assertion.
