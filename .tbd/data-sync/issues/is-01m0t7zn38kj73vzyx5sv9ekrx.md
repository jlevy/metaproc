---
type: is
id: is-01m0t7zn38kj73vzyx5sv9ekrx
title: "PR #33 review R1: disclose command-step concurrency change"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:47.815Z
updated_at: 2026-08-24T15:59:47.815Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. run_process.py command-backed code steps moved from accidental event-loop serialization to run-owned executor concurrency, without write-boundary protection. Document the concurrency and shared-process_dir risk in CHANGELOG and operator reference.
