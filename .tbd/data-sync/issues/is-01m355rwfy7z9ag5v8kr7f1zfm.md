---
type: is
id: is-01m355rwfy7z9ag5v8kr7f1zfm
title: "PR 92 R5: preserve credential owner access under restrictive umask"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m355h6et8q600r0pv9c30k49
created_at: 2026-09-22T18:24:11.254Z
updated_at: 2026-09-22T18:59:41.379Z
closed_at: 2026-09-22T18:59:41.379Z
close_reason: Fixed in e908f70. All review dispositions posted on PR 92; full make verify passed (5069 passed, 34 skipped) and GitHub lint, distribution, and Python 3.12-3.14 CI passed. PR 92 merged as 7de48d1 into claude/collect-input-reuse.
resolution: null
duplicate_of: null
---
PR 92 review R5: io/secret_io.py:62 publishes mode 0000 under umask 0777, whereas old chmod restored 0600. Apply fchmod before secret bytes and test restrictive umask.
