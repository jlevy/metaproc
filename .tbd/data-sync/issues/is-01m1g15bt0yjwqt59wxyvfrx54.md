---
type: is
id: is-01m1g15bt0yjwqt59wxyvfrx54
title: "PR62 review F3c: pressure 4 never counts as recovered"
kind: bug
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies: []
parent_id: is-01m1g159htcr2kbsgb0mnzkyx2
created_at: 2026-09-02T03:03:52.383Z
updated_at: 2026-09-02T03:16:41.454Z
closed_at: 2026-09-02T03:16:41.454Z
close_reason: Fixed in 1333fd5 on codex/runpool-host-safety-plan (pull request 62); disposition recorded in the review addendum.
resolution: null
duplicate_of: null
---
State machine: critical exits only when platform alarm is clear and headroom held for a confirmation window (TestPressureFourNeverRecovers). (review F-id in title; PR 62; plan files under docs/project/specs/active/)
