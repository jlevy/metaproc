---
type: is
id: is-01m4myb5827yr78k8vdzyb34sp
title: "PR #106 B1: in manual mode one quota failure halves the pool and it never heals"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10
labels: []
dependencies: []
parent_id: is-01m4my9r60j9s9nkm8t1rnam6f
hold: null
hold_until: null
created_at: 2026-10-11T07:37:54.177Z
updated_at: 2026-10-11T13:58:47.216Z
started_at: 2026-10-11T13:13:54.695Z
closed_at: 2026-10-11T13:58:47.215Z
close_reason: fixed in 97c004d (manual mode logs ignored, ceiling untouched); rate-limit analogue tracked as mp-tglp; dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786498
resolution: null
duplicate_of: null
---
Medium. Review B https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521. pool.py:824-829 applies _apply_quota_backoff regardless of _operator_mode_override; manual monitor branch (pool.py:1851-1854) skips _adjust_concurrency which owns provider recovery (pool.py:2275-2298). ScaleOverride documents manual as freezing both governors (status.py:176-177). Fix (pick one): (a) in manual mode count, log a distinct action, keep pause_for_quota, leave _provider_ceiling; or (b) run provider recovery from the manual branch. Add test; document in arch-runpool.md.
