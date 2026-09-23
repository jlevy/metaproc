---
type: is
id: is-01m0zfp2wxy4t1c76mez33z2v7
title: Add an arch-doc date-drift check to devtools
kind: feature
status: closed
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:50:35.037Z
updated_at: 2026-08-27T17:38:40.301Z
closed_at: 2026-08-27T17:38:40.301Z
close_reason: "Added devtools/check_doc_dates.py, wired into make lint-check, with tests. Caught the drift the bead documented: arch-claude-code-harness.md claimed 2026-05-23, arch-testing.md 2026-07-26, arch-file-io-utilities.md 2026-08-09. All eight dated docs bumped. Compares whitespace-normalized content between commits rather than git diff -w, so Flowmark reflows do not count as edits."
resolution: null
duplicate_of: null
---
development.md states the convention ('When you make non-trivial changes, bump the last updated date above') but nothing enforces it. Measured drift on a7cb65b: arch-runpool.md claims 2026-05-23 with substantive commits on 2026-08-21 ('correct four stale claims'); arch-claude-code-harness.md claims 2026-05-23 vs 2026-08-09; arch-testing.md 2026-07-26 vs 2026-08-09; arch-file-io-utilities.md 2026-08-09 vs 2026-08-22. Add a check next to devtools/check_links.py comparing each doc's last-updated against git log --no-merges -1, ignoring format-only commits, and wire it into make verify.
