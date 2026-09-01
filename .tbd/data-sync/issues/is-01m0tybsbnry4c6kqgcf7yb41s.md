---
type: is
id: is-01m0tybsbnry4c6kqgcf7yb41s
title: "Review PR #37: map composite scopes in-process"
kind: task
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T22:30:54.068Z
updated_at: 2026-09-01T05:22:10.298Z
closed_at: 2026-09-01T05:22:10.284Z
close_reason: "PR #37 was closed and superseded by the consolidated mapped-scope branch, which landed via PRs #48/#49. The round-1 and round-2 findings recorded here were addressed in that consolidation; nothing on #37 remains to review."
resolution: null
duplicate_of: null
---
Phase 2 mapped-composite implementation. Round-1 review posted 2026-08-24. Must-fix before undraft: B1 child scope does not rebind RUN_ID/RUNS_DIR so every mapped item's {{run.dir}} is the parent dir (cross-item contamination, passes validation); B2+B3 non-CLIError abandons siblings (gather has no return_exceptions) + unbounded scope concurrency/FD exhaustion; B6 split graph.py failure-propagation change out (head commit, untested, contradicts shipped docs). Also B4 '..' is a legal item key. Contract items 1/7/8 open.

## Notes

Round-2 review + holistic doc + handoff note all posted on #37. Findings re-verified OPEN at head 49064f0 (three commits past reviewed 0995cdd; the two new code commits — 941f2aa preflight agent-only, 981295f dep_state fingerprints — are independent L0 finds, not remediation). Undraft gate: mp-xkvz, mp-cr12, mp-s070, mp-ledg. Rebase gate: mp-wzdl. PR is ready to hand off; lands last per mp-qq8c sequence.
