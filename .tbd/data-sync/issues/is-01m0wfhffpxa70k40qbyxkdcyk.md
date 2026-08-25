---
type: is
id: is-01m0wfhffpxa70k40qbyxkdcyk
title: Extract nested composite scope logs from a parent trace
kind: bug
status: closed
priority: 0
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - operator-smoke
dependencies: []
parent_id: is-01m0vhs620ptcvxv074ccx88z4
created_at: 2026-08-25T12:50:20.789Z
updated_at: 2026-08-25T13:19:02.524Z
closed_at: 2026-08-25T13:19:02.524Z
close_reason: "Fixed at b5c4721; regressions, full 4385-test gate, pre-push gate, and successful-run operator replay passed. PR #37 has no checks because its stacked base is not main; exact-pin consumer CI remains the independent gate."
resolution: null
duplicate_of: null
---
Parent trace extraction on a successful mapped GTIA run omitted the nested query-plan child Gemini logs; extracting directly at the child scope found them. Make trace extraction recursively discover nested run scopes generically, attach scope.path, namespace span IDs/parents, and make cross-source links scope-aware so a parent trace represents the full process without a workflow-specific walker.
