---
type: is
id: is-01m0t7zs3etjttp22nytn7abcn
title: "PR #33 review R9: require context at executable leaf boundaries"
kind: bug
status: open
priority: 2
version: 6
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
hold: paused
created_at: 2026-08-24T15:59:51.918Z
updated_at: 2026-09-10T18:45:38.742Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. Five leaf helpers retain RunExecutionContext | None defaults, allowing a forgotten call-site argument to silently bypass shared admission and pooling. Make context required at leaf-layer APIs once compatibility call sites are resolved.

## Notes

Explicitly deferred compatibility cleanup. Production recursive call sites pass one context and tests prove shared admission; making context mandatory would widen direct library/test APIs without changing the current runtime. Revisit only in a separate API cleanup after smoke.
