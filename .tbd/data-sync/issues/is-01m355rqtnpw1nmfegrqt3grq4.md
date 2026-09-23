---
type: is
id: is-01m355rqtnpw1nmfegrqt3grq4
title: "PR 92 R2: preserve diagnostic copy permissions and metadata"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m355h6et8q600r0pv9c30k49
created_at: 2026-09-22T18:24:06.482Z
updated_at: 2026-09-22T18:59:41.271Z
closed_at: 2026-09-22T18:59:41.271Z
close_reason: Fixed in e908f70. All review dispositions posted on PR 92; full make verify passed (5069 passed, 34 skipped) and GitHub lint, distribution, and Python 3.12-3.14 CI passed. PR 92 merged as 7de48d1 into claude/collect-input-reuse.
resolution: null
duplicate_of: null
---
PR 92 review R2: dispatch/slot_coordinator.py:565 replaces copy2 with copyfile_atomic, widening 0600 to 0644. Stage copy2 with atomic_output_file and regression-test modes, timestamps, failed copy.
