---
type: is
id: is-01kzkvpyhhn82qnb9ga444b3mb
title: "PR #15 review S2: keep sampling log-open failures best-effort"
kind: bug
status: closed
priority: 3
version: 3
labels:
  - pr-review
dependencies: []
parent_id: is-01kzkvezcf2f56523788t1v091
created_at: 2026-08-09T18:14:11.248Z
updated_at: 2026-08-09T18:21:58.094Z
closed_at: 2026-08-09T18:21:58.094Z
close_reason: "Fixed in ada6da2; focused tests, make verify, and all PR #15 GitHub checks passed."
---
PR #15 non-blocking review suggestion S2. src/metaproc/engine/resource_sampling.py: ResourceEventLogger open failures currently abort the owning code step even though resource sampling is observability-only. Degrade on OSError and continue sampling without a logger, with regression coverage. Review: https://github.com/jlevy/metaproc/pull/15#issuecomment-5232970130
