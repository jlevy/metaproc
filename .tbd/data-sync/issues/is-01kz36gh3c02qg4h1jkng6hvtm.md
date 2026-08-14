---
type: is
id: is-01kz36gh3c02qg4h1jkng6hvtm
title: "PR #9 review PR9-R1: preserve exact identity in GCP inventory and status"
kind: bug
status: closed
priority: 1
version: 6
labels:
  - pr-review
  - pr-9
dependencies: []
parent_id: is-01kz36g3q9wbmhwnwcs170y1s3
created_at: 2026-08-03T06:55:49.868Z
updated_at: 2026-08-03T07:06:44.759Z
closed_at: 2026-08-03T07:06:44.756Z
close_reason: "Fixed and hardened: modern inventory groups use exact hash keys, exact RUN_ID is recovered only when structured metadata verifies against that hash, dot/underscore/dash identities round-trip, missing metadata stays distinct, modern and legacy groups sharing a display value cannot overwrite or combine, and local status preserves the exact directory name. Six focused inventory tests plus lookup/status tests pass."
---
Formal review PR9-R1 (High), PR #9. src/metaproc/commands/gcp.py:1336 and :355. gcp runs groups modern jobs by lossy metaproc-run-id, collapsing exact identities and emitting default dot-separated IDs in a non-round-trippable form; local status also substitutes the lossy label. Fix exact identity recovery/grouping with verified hash, legacy fallback, and local exact display. Add collision, round-trip, fallback, and local-display tests.
