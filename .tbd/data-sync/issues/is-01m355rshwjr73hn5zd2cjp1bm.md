---
type: is
id: is-01m355rshwjr73hn5zd2cjp1bm
title: "PR 92 R3: clean writability probes after write failure"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m355h6et8q600r0pv9c30k49
created_at: 2026-09-22T18:24:08.243Z
updated_at: 2026-09-22T18:59:41.323Z
closed_at: 2026-09-22T18:59:41.323Z
close_reason: Fixed in e908f70. All review dispositions posted on PR 92; full make verify passed (5069 passed, 34 skipped) and GitHub lint, distribution, and Python 3.12-3.14 CI passed. PR 92 merged as 7de48d1 into claude/collect-input-reuse.
resolution: null
duplicate_of: null
---
PR 92 review R3: engine/preflight.py:143 and commands/auth_check.py:704 leak descriptors and files when os.write fails. Set always_clean=True and test both paths.
