---
type: is
id: is-01m0vmt18k4yn95zac880rb6jq
title: "PR #39 N1: restore whole-scheduler complexity coverage"
kind: task
status: open
priority: 3
version: 1
labels: []
dependencies: []
parent_id: is-01m0tybt1wpr25671rtk5bstyr
created_at: 2026-08-25T05:03:09.587Z
updated_at: 2026-08-25T05:03:09.587Z
---
The deterministic aligned-membership guard covers the known key-set hazard but no longer times a complete scheduling pass above the scale knee. Add a deterministic work-count oracle for clause_status/live-attempt/settlement/command projection, or a deliberately loose end-to-end complexity backstop, when the reference scheduler is promoted beyond its current oracle role. Review: PR #39 issuecomment-5402359858.
