---
type: is
id: is-01m0wfhffpxa70k40qbyxkdcyk
title: Extract nested composite scope logs from a parent trace
kind: bug
status: closed
priority: 0
version: 4
spec_path: null
labels:
  - operator-smoke
dependencies: []
parent_id: is-01m0vhs620ptcvxv074ccx88z4
created_at: 2026-08-25T12:50:20.789Z
updated_at: 2026-08-25T17:00:19.630Z
closed_at: 2026-08-25T13:19:02.524Z
close_reason: "Fixed at b5c4721; regressions, full 4385-test gate, pre-push gate, and successful-run operator replay passed. PR #37 has no checks because its stacked base is not main; exact-pin consumer CI remains the independent gate."
resolution: null
duplicate_of: null
---
Parent trace extraction for a mapped process can omit agent logs inside nested composite scopes even though direct child-scope extraction finds them. Recursively discover nested run scopes, attach scope.path, namespace span identifiers and parents, and make cross-source links scope-aware so the parent trace represents the full process without a workflow-specific walker.

## Notes

The generic nested-scope trace regression and full verification passed on the prior integration head. Revalidate on the clean replacement head.
