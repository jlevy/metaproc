---
type: is
id: is-01m4nnhqkv0tpn1hk5ke847b9k
title: "PR #106 C2: manual-mode rate-limit cut (mp-tglp) now reachable from scalar leaves"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m4nnhe30ebaqnt9h1p6m4xxs
created_at: 2026-10-11T14:23:26.842Z
updated_at: 2026-10-11T14:36:29.815Z
closed_at: 2026-10-11T14:36:29.814Z
close_reason: Deferred to open mp-tglp (note records widened reach). Reply https://github.com/jlevy/metaproc/pull/106#issuecomment-6110124542
resolution: duplicate
duplicate_of: is-01m4nkmdjjhsaksdk891gmq5p7
---
Low. Review C https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5483637641. pool.py:936-971 _apply_rate_limit_backoff cuts in manual mode; scalar leaves now report failures (run_process.py:3632-3635). Pre-existing, tracked as mp-tglp.
