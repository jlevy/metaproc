---
type: is
id: is-01kzkvf91gzjfrkdfpzr7dg41r
title: "PR #15 review R2: use canonical item keys for samples"
kind: bug
status: closed
priority: 2
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01kzkvezcf2f56523788t1v091
created_at: 2026-08-09T18:09:59.855Z
updated_at: 2026-08-09T18:21:58.074Z
closed_at: 2026-08-09T18:21:58.074Z
close_reason: "Fixed in ada6da2; focused tests, make verify, and all PR #15 GitHub checks passed."
---
PR #15 review finding R2 (Medium). src/metaproc/commands/run_parallel.py:979,991 and src/metaproc/commands/run_step.py:353,371. Attribute resource samples to the resolved for_each.key and add custom-key run-step/run-parallel regression coverage. Review: https://github.com/jlevy/metaproc/pull/15#issuecomment-5232970130
