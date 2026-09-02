---
type: is
id: is-01m1g15dsar38qr09e79ca2a1b
title: "PR62 review F3g: monitored-mode kill mechanics not in Design"
kind: bug
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies: []
parent_id: is-01m1g159htcr2kbsgb0mnzkyx2
created_at: 2026-09-02T03:03:54.409Z
updated_at: 2026-09-02T03:16:42.835Z
closed_at: 2026-09-02T03:16:42.835Z
close_reason: Fixed in 1333fd5 on codex/runpool-host-safety-plan (pull request 62); disposition recorded in the review addendum.
resolution: null
duplicate_of: null
---
SIGSTOP victim root before enumerating; deepest-first; shared batch grace; SIGCONT after SIGTERM; owned mode uses group signalling (memory_guard.py:2165-2226). (review F-id in title; PR 62; plan files under docs/project/specs/active/)
