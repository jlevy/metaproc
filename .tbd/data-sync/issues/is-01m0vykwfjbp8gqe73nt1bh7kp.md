---
type: is
id: is-01m0vykwfjbp8gqe73nt1bh7kp
title: Preserve primary failure when RunPool shutdown also fails
kind: bug
status: closed
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies: []
parent_id: is-01m0vhs620ptcvxv074ccx88z4
created_at: 2026-08-25T07:54:33.841Z
updated_at: 2026-08-25T19:28:49.292Z
closed_at: 2026-08-25T19:28:49.291Z
close_reason: Fixed with primary-error preservation across RunPool cleanup failure and focused coverage.
resolution: null
duplicate_of: null
---
The new run-owned RunPool is closed in a finally block. If orchestration and pool shutdown both fail, the cleanup exception can replace the actionable pipeline failure. Preserve the primary exception, log/attach the cleanup failure, and retain cleanup failure as terminal when no primary failure exists. Add focused lifecycle coverage.

## Notes

Fixed: cleanup failure is attached to the primary orchestration exception and cannot replace it; cleanup-only failure remains terminal. Focused regression and full verification pass.
