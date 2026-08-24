---
type: is
id: is-01m0v08wy0cem0nwa7zeejr8qd
title: "PR #35 I11: lifecycle fast-follows after merge"
kind: task
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:16.575Z
updated_at: 2026-08-24T23:04:16.575Z
---
Four tracked defects from the round-2 review, none merge-blocking: (N3) _observed_descendants unbounded and re-walked at 10Hz — O(1e5) psutil lookups per kill for a long agent; prune + stop re-walking per poll. (N4) descendants first spawned in the last <=10s before leader exit are never recorded on the pool path — fence by pgid+create_time enumeration instead of prior observation. (N5) 'cancelled' outranks running/failed in _write_process_status and is carried forward, poisoning later partial runs. (N6) submit() after the kill sentinel raises RuntimeError that the scheduler converts into synthetic failures + retries, burning every item's retry budget. Review: pull/35 comment; holistic ledger #11.
