---
type: is
id: is-01m0txfcvht87nbn777cm2bstv
title: Decide 0.3.0 disposition of the pool-admission attempt-history divergence
kind: task
status: closed
priority: 1
version: 2
labels:
  - release,execution-model
dependencies: []
parent_id: is-01m0tx34t3n8g39jjbhzdrrpwf
created_at: 2026-08-24T22:15:23.760Z
updated_at: 2026-08-25T02:38:01.226Z
closed_at: 2026-08-25T02:38:01.226Z
close_reason: Decided option 2 (ship and document) for 0.3.0, as recommended. Live execution is correct — the pool-exhausted path does not consume the production retry budget — so the blast radius is replay and post-hoc analysis, not run outcomes. Re-cutting the fan-out admission path in a release whose purpose is to stabilize what already landed would carry more risk than the defect. Recorded as a known gap in the Compatibility section of docs/releases/v0.3.0.md, citing mp-ux0f and mp-f5m5, which stay open to carry the fix in the follow-up release.
resolution: null
duplicate_of: null
---
## What was confirmed

The defect described in mp-ux0f and mp-f5m5 is present on main at 6819ddd and therefore ships in 0.3.0. Verified directly in `src/metaproc/commands/run_parallel.py`:

- line ~1446: `mark_running_at(...)` writes running state
- line ~1454: `write_attempt_at(AttemptRecord(...))` persists the attempt
- line ~1528: `acquire_slot(...)` runs, and this is what raises `PoolSlotUnavailableError`

So a task blocked purely on credential-pool capacity is recorded as running with a persisted attempt before admission is granted. When admission then fails, the fan-out path synthesizes a failed record and reschedules the same attempt.

## Impact

Live execution is correct: the pool-exhausted path deliberately does not consume the production retry budget, so a waiting run keeps waiting. The divergence is in replay. Replay counts every lost record against `max_attempts`, so a run that merely waited on capacity several times can replay as failed while production considered it healthy.

That matters for this release specifically, because durable per-attempt history is the headline feature of 0.3.0 and the CHANGELOG states: "Replay consumes the exact history when present and retains status-based compatibility for historical run trees." On the pool-admission path that parity does not hold.

## Decision needed

Pick one before tagging:

1. **Fix first.** mp-ux0f prescribes the fix: move task state through `admission_wait` without writing an attempt fact, and create `AttemptStarted` only after a launch claim is admitted. Correct, but it touches the fan-out launch path in the same release that rewrote it, which is its own risk.
2. **Ship and document.** Add a known-limitation note to the 0.3.0 notes scoping the replay-parity claim to non-pool-exhausted paths. Live behavior is unaffected, so this is defensible for a 0.x minor.

Recommendation: option 2. The production path is correct, the blast radius is replay and post-hoc analysis rather than run outcomes, and re-cutting the fan-out admission path is exactly the kind of change that should not ride along in a release whose purpose is to stabilize what already landed. Fix it in the follow-up release alongside the GTIA stack, which reworks this area anyway.

## Also note

mp-f5m5 currently carries `hold: paused`. A P1 defect in code that is about to ship should not be sitting paused without a recorded reason. Unpause it or record why it is deferred.
