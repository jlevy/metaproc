---
type: is
id: is-01kzkvf98z1z54mzyhfxry5vj7
title: "PR #15 review R3: exclude concurrent sibling subprocesses"
kind: bug
status: closed
priority: 2
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01kzkvezcf2f56523788t1v091
created_at: 2026-08-09T18:10:00.094Z
updated_at: 2026-08-09T18:21:58.080Z
closed_at: 2026-08-09T18:21:58.080Z
close_reason: "Fixed in ada6da2; focused tests, make verify, and all PR #15 GitHub checks passed."
---
PR #15 review finding R3 (Medium). src/metaproc/commands/run_process.py:977,2342; src/metaproc/engine/resource_sampling.py:54; src/metaproc/osutils/psutil_sampler.py:102,146. Ensure in-process handler sampling excludes pre-existing child subtrees while retaining descendants created by the handler; add a concurrency regression test. Review: https://github.com/jlevy/metaproc/pull/15#issuecomment-5232970130
