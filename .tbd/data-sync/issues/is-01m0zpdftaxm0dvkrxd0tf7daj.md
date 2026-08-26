---
type: is
id: is-01m0zpdftaxm0dvkrxd0tf7daj
title: Code fan-out bare resume re-invokes completed items
kind: bug
status: open
priority: 1
version: 1
labels:
  - resume
  - fanout
dependencies: []
created_at: 2026-08-26T18:48:13.385Z
updated_at: 2026-08-26T18:48:13.385Z
---
The code fan-out discovery path returns both actionable and filtered completed items, and the executor invokes every row. A bare same-RUN_ID resume therefore creates new attempts for completed code fan-out items such as terminal review projections. Add a focused regression and retain validated completed items unless force or invalidation requires a rerun.
