---
type: is
id: is-01m355rycracg9qppw8h3qnfgk
title: "PR 92 R6: distinguish exclusive creation from atomic publication"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m355h6et8q600r0pv9c30k49
created_at: 2026-09-22T18:24:13.204Z
updated_at: 2026-09-22T18:59:41.395Z
closed_at: 2026-09-22T18:59:41.395Z
close_reason: Fixed in e908f70. All review dispositions posted on PR 92; full make verify passed (5069 passed, 34 skipped) and GitHub lint, distribution, and Python 3.12-3.14 CI passed. PR 92 merged as 7de48d1 into claude/collect-input-reuse.
resolution: null
duplicate_of: null
---
PR 92 review R6: arch-file-io-utilities.md:115 wrongly promises complete-content atomic visibility for open x and mkdir claims. Correct documentation without changing runtime claims or gate scope.
