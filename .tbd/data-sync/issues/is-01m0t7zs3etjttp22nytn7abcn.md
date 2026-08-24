---
type: is
id: is-01m0t7zs3etjttp22nytn7abcn
title: "PR #33 review R9: require context at executable leaf boundaries"
kind: bug
status: open
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0rs7df0g28zgnsykar366kb
created_at: 2026-08-24T15:59:51.918Z
updated_at: 2026-08-24T19:03:43.172Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. Five leaf helpers retain RunExecutionContext | None defaults, allowing a forgotten call-site argument to silently bypass shared admission and pooling. Make context required at leaf-layer APIs once compatibility call sites are resolved.

## Notes

Deferred from PR #33 review R9 until the executable-leaf API settles across mapped scopes. Make context mandatory only when compatibility call sites are known, so this typing cleanup does not force premature surface churn.
