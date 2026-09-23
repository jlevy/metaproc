---
type: is
id: is-01m1g15cvy3ane1r8nr3x5e8b4
title: "PR62 review F3e: sentinel self-health (scheduling priority) missing"
kind: bug
status: closed
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies: []
parent_id: is-01m1g159htcr2kbsgb0mnzkyx2
created_at: 2026-09-02T03:03:53.469Z
updated_at: 2026-09-02T03:16:42.167Z
closed_at: 2026-09-02T03:16:42.167Z
close_reason: Fixed in 1333fd5 on codex/runpool-host-safety-plan (pull request 62); disposition recorded in the review addendum.
resolution: null
duplicate_of: null
---
Add sentinel self-health paragraph: THREAD_PRECEDENCE_POLICY 63 + timeshare=0 on macOS; not QoS, not nice; priority does not fix page-wait starvation (memory_guard.py:872-944). (review F-id in title; PR 62; plan files under docs/project/specs/active/)
