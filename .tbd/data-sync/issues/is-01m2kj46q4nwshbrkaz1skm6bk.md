---
type: is
id: is-01m2kj46q4nwshbrkaz1skm6bk
title: "PR #83 review P2-R2: --step-variant on a top-level composite is accepted then ignored"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:45.314Z
updated_at: 2026-09-15T22:13:45.314Z
---
PR #83 part 2 R2 (Medium). Unpinned composite override recorded then ignored when planning the child; on a pinned composite it replaces the pin. engine/build_plan.py:111-129, 875-887; commands/run_process.py:522-536.
