---
type: is
id: is-01m0tk2r8vcgg92j29a6gf6gch
title: "PR #37 review R2: preserve primary binding in parent task state"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01m0tk2b98fs83zr2q0f6gc7rt
created_at: 2026-08-24T19:13:43.707Z
updated_at: 2026-08-24T19:23:35.772Z
closed_at: 2026-08-24T19:23:35.772Z
close_reason: Rebutted — ProcessStep validation already requires bind_fields to include bind. The requested red test is rejected while constructing ProcessStep, before runtime discovery or task-state persistence, so the alleged valid configuration cannot exist.
resolution: null
duplicate_of: null
---
Formal review https://github.com/jlevy/metaproc/pull/37#pullrequestreview-5011619572. run_process.py:2363-2365 persists raw bind_fields, so a valid mapping that omits bind from bind_fields loses the primary item value. Persist canonical discovery item fields and test empty bind_fields.
