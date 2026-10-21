---
type: is
id: is-01m4myb6n22nt05j6w1fwrqc5e
title: "PR #106 B3: failing event write inside the governor replaces the item verdict and leaves ceiling lowered but unenforced"
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
created_at: 2026-10-11T07:37:55.616Z
updated_at: 2026-10-11T13:58:47.757Z
started_at: 2026-10-11T13:13:55.407Z
closed_at: 2026-10-11T13:58:47.756Z
close_reason: fixed in 19d6e7b (enforce before logging; guarded event writes); dispositions https://github.com/jlevy/metaproc/pull/106#issuecomment-6109786498
resolution: null
duplicate_of: null
---
Low. Review B https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521. pool.py:997-1005 writes quota_backoff via unguarded EventLogger._write (events.py:46-52) between lowering _provider_ceiling and _set_capacity. Fix: enforce capacity before the diagnostic write and guard governor event writes with except OSError like _record_completion (pool.py:1807-1810).
