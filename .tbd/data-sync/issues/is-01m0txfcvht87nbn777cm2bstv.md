---
type: is
id: is-01m0txfcvht87nbn777cm2bstv
title: Decide 0.3.0 disposition of the pool-admission attempt-history divergence
kind: task
status: closed
priority: 1
version: 5
labels:
  - release,execution-model
dependencies: []
parent_id: is-01m0tx34t3n8g39jjbhzdrrpwf
created_at: 2026-08-24T22:15:23.760Z
updated_at: 2026-08-25T17:01:15.092Z
closed_at: 2026-08-25T17:01:15.092Z
close_reason: Shipped and documented the replay limitation rather than recutting the launch path during the stable release. The production path remained correct; the follow-up defect stays tracked separately.
resolution: null
duplicate_of: null
---
Decide whether the current release should fix or document the pool-admission attempt-history divergence. Live execution is correct, but replay can count capacity waits against max_attempts because the attempt record is written before admission. Prefer documenting the known limitation for the stable release and fixing the launch path in the separately reviewed follow-up.
