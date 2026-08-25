---
type: is
id: is-01m0txtb8gym93qyy8atwa6j8d
title: Skip harness preflight for adapterless active plans
kind: bug
status: in_progress
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels: []
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T22:21:22.576Z
updated_at: 2026-08-24T22:24:37.268Z
---
The pinned GTIA v3.0-pre L0 process contains only composite and code leaves, but run-process preflights the default claude-code-cli adapter attached to every resolved step. That launches claude --version and emits drift warnings despite no agent work. Filter launch preflight to active agent steps so deterministic code-only/composite runs have no irrelevant harness CLI side effect; preserve preflight for actual agent leaves and cover both cases.

## Notes

TDD regression proved adapterless code/composite plans incorrectly invoked the default adapter preflight. The implementation filters launch preflight to active mode: agent steps and retains once-per-adapter behavior for actual agent steps. Focused run-process suite: 91 passed. Full make verify: 4,356 passed, 8 skipped; lint, types, docs, browser, audits, distribution, and installed-wheel smoke green. Keep open until the pinned GTIA L0 consumer reruns without the Claude drift preflight and exact-head CI is green.
