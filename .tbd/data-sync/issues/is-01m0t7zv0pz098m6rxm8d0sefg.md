---
type: is
id: is-01m0t7zv0pz098m6rxm8d0sefg
title: "PR #34 review 1: keep credential slots inside the run tree"
kind: bug
status: closed
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3r59m3mpwg54j5s6qhf
created_at: 2026-08-24T15:59:53.877Z
updated_at: 2026-08-24T16:38:02.828Z
closed_at: 2026-08-24T16:38:02.828Z
close_reason: "Fixed in e3f177b; exact-head make verify passed (4,318 passed, 8 skipped) and disposition published on PR #34."
resolution: null
duplicate_of: null
---
Review https://github.com/jlevy/metaproc/pull/34#issuecomment-5397585053. run_process.py rebinds PoolDispatchConfig.run_id to logical spec-name/run-context, relocating .state/auth outside the actual run tree and diverging orchestrator/worker audit paths. Introduce a path-relative scope identity and test scalar and fan-out slot paths under run_dir.
