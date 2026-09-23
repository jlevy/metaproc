---
type: is
id: is-01m0vr1nkd00q7j71pj6f6at7z
title: "PR #35 R2 N9: fence sampled-command process-group cleanup"
kind: bug
status: closed
priority: 2
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-25T05:59:45.516Z
updated_at: 2026-08-25T06:46:53.689Z
closed_at: 2026-08-25T06:46:53.689Z
close_reason: "Fixed in 4855c8d; exact-head pre-push make verify passed with 4,351 tests and 8 skips, and the per-finding disposition is published at PR #35 comment 5406589771."
resolution: null
duplicate_of: null
---
_terminate_process_tree uses unfenced killpg after LocalBackend added PID/create-time ownership fencing for the same reuse hazard. Reuse a minimal identity-fenced cleanup primitive or prove the synchronous command path cannot hit PID reuse; add regression coverage.
