---
type: is
id: is-01m4myad14e1ey2dv9chqsgjs2
title: "PR #106 A3: per-minute quota retries take the full default budget at a flat 60 s floor with no jitter"
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
created_at: 2026-10-11T07:37:29.379Z
updated_at: 2026-10-11T13:58:44.969Z
started_at: 2026-10-11T13:13:52.127Z
closed_at: 2026-10-11T13:58:44.968Z
close_reason: fixed in b1e21d7 (MAX_QUOTA_RETRIES_DEFAULT=3 via max_retries_for; reset-clock branch keeps declared budget); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786278
resolution: null
duplicate_of: null
---
Medium. Review A https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482282352. retry.py:520-530 gives QUOTA_EXHAUSTED the declared max_retries (default 12); retry_backoff_s (retry.py:710-719) is max(policy, 60), flat 60 s with no jitter; each retry is a paid multi-minute session. Fix (pick one): separate cap for per-minute quota retries, or growing floor with jitter; document episode cost in arch-runpool.md.
