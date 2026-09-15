---
type: is
id: is-01m2h566yhqdxbdjcj74hsrr5p
title: Preserve pooled native session records before credential-slot teardown
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m26a2gvmth1jjgrkg9fs1vha
created_at: 2026-09-14T23:49:10.736Z
updated_at: 2026-09-15T00:02:06.624Z
closed_at: 2026-09-15T00:02:06.606Z
close_reason: "Implemented in PR #82: pooled Codex and opt-in Claude native session sets are preserved before slot teardown under an isolated private namespace with descriptor-safe all-or-nothing publication. Review findings were addressed, privacy/retention docs and the governing plan were reconciled, GTIA compatibility checks passed, and make verify passed with 4,704 tests."
resolution: null
duplicate_of: null
---
GitHub issue #81 / PR #82 foundation for direct raw-log access. Preserve complete Codex native rollouts and opt-in Claude transcripts from pooled credential slots before teardown, isolate them under .logs/native, enforce descriptor-safe containment and private permissions, keep generic captured-stream readers from double-counting them, and document their privacy and retention contract. This bounded slice does not close mp-83g2: attempt locators, raw pre-filter stdout/stderr, direct UI/open/tail affordances, all provider/state/cloud mapping, and expiry visibility remain open.
