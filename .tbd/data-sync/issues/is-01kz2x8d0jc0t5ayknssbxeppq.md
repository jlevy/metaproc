---
type: is
id: is-01kz2x8d0jc0t5ayknssbxeppq
title: "PR #8 review MP8-05: preserve partial resource coverage"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:06.354Z
updated_at: 2026-08-03T04:21:01.407Z
closed_at: 2026-08-03T04:21:01.407Z
close_reason: Fixed resource coverage and rate-limit taxonomy accounting with focused regression tests; Python lint and type checks pass.
---
src/metaproc/models/resource_budget.py:422, src/metaproc/logutil/resource_event_extract.py:395, and src/metaproc/logutil/agent_provider_meters.py:77. Preserve measured zero, propagate unknown matching evidence, and emit unmeasured turn observations for interrupted initialized sessions. Add mixed-coverage tests.
