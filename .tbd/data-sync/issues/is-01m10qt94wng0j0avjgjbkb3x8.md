---
type: is
id: is-01m10qt94wng0j0avjgjbkb3x8
title: "PR #49 review R30: Separate fan-out disposition from authored item context"
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies: []
parent_id: is-01m10mm4vpgbqgrjqx4dbjee41
created_at: 2026-08-27T04:31:55.546Z
updated_at: 2026-08-27T04:36:02.253Z
closed_at: 2026-08-27T04:36:02.253Z
close_reason: "Fixed in 9d34c1f; full make verify passed with 4,493 tests and GitHub CI completed 5/5 green. Published per-finding dispositions on PR #49."
resolution: null
duplicate_of: null
---
PR #49 review finding R30 at src/metaproc/engine/discovery.py and src/metaproc/commands/run_process.py: framework filter metadata overwrote the legal authored bind field named reason, and source-terminal items could be re-authorized during run-plan refresh. Fix by separating disposition from context, preserving authored fields, retaining completed/cached/running keys, excluding terminal keys, and adding regressions.
