---
type: is
id: is-01m0z03c1r90y3g7dpgerahhcq
title: Make bounded-shutdown regression scheduler-independent
kind: bug
status: in_progress
priority: 1
version: 2
labels:
  - runpool
dependencies: []
created_at: 2026-08-26T12:18:13.176Z
updated_at: 2026-08-26T12:18:16.883Z
---
The wedged-backend shutdown regression asserts a 50 ms internal cleanup bound using an unrelated 250 ms outer wall-clock timeout. Under a loaded parallel test runner the expected cleanup diagnostic is emitted, yet the outer guard can expire before the shutdown task is rescheduled. Synchronize the test on entry into the contractual bounded wait, assert the configured cleanup bound directly, and retain a generous deadlock guard without weakening production behavior.
