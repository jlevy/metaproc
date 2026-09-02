---
type: is
id: is-01m1g15as09544m51hvvhjhnw2
title: "PR62 review F3a: producer pause has no duty-cycle cap"
kind: bug
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies: []
parent_id: is-01m1g159htcr2kbsgb0mnzkyx2
created_at: 2026-09-02T03:03:51.328Z
updated_at: 2026-09-02T03:16:40.765Z
closed_at: 2026-09-02T03:16:40.764Z
close_reason: Fixed in 1333fd5 on codex/runpool-host-safety-plan (pull request 62); disposition recorded in the review addendum.
resolution: null
duplicate_of: null
---
Add invariant: every producer pause has a wall-clock cap and minimum service window (guard --max-pause-s 8 s, --min-run-s 1.5 s; memory_guard.py:2032-2060). (review F-id in title; PR 62; plan files under docs/project/specs/active/)
