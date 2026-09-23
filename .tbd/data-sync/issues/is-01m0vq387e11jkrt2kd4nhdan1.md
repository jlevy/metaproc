---
type: is
id: is-01m0vq387e11jkrt2kd4nhdan1
title: "PR #34 R2 B4: disclose auth transport API shape change"
kind: task
status: closed
priority: 3
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3r59m3mpwg54j5s6qhf
created_at: 2026-08-25T05:43:08.782Z
updated_at: 2026-08-25T05:50:38.970Z
closed_at: 2026-08-25T05:50:38.970Z
close_reason: null
resolution: null
duplicate_of: null
---
Document that OrchestratorDispatchConfig replaced scalar auth fields with one AuthPoolFlags payload; this is an intentional internal Python API shape change, not only a bug fix. Review: https://github.com/jlevy/metaproc/pull/34#issuecomment-5402358604
