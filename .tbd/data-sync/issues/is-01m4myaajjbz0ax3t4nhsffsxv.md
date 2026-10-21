---
type: is
id: is-01m4myaajjbz0ax3t4nhsffsxv
title: "PR #106 A1: governor cuts on any quota substring, so hard caps and Disk quota exceeded halve the pool"
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
created_at: 2026-10-11T07:37:26.858Z
updated_at: 2026-10-11T13:58:44.404Z
started_at: 2026-10-11T13:13:51.452Z
closed_at: 2026-10-11T13:58:44.403Z
close_reason: fixed in 2b0eeb3 (governor cuts only when rate_quota, set from retry.is_rate_quota); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786278
resolution: null
duplicate_of: null
---
Medium. Review A https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482282352. src/metaproc/runpool/pool.py:826-827 calls _apply_quota_backoff for every quota_exhausted; classify_failure (retry.py:474-488) gives that class to any 'quota' string, so hard caps (Claude out of extra usage, OpenAI billing) and EDQUOT halve the pool and hold recovery 300 s. Fix: cut only on the per-minute signal; test that a hard-cap string leaves current_max_concurrency unchanged.
