---
type: is
id: is-01m260z0fgtg9ypvj5tk7bv1j5
title: "PR 75 F5: review host-safety rollout and mixed-client guarantees"
kind: task
status: open
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T16:03:41.679Z
updated_at: 2026-09-10T16:21:09.717Z
---
Review decision for existing host-safety work: count slots with per-caller prefix limits do not create one byte budget; scalar admission fails open after 60s/OSError; startup peaks are unreserved and telemetry shares the orchestrator event loop. Require broker claim-v2 compatibility, startup pacing, external-pressure attribution, sentinel failure tests and coherent mixed-client caps before advertising host safety. Implementation remains mp-qigc/mp-g3si and the active host-safety plan; no duplicate runtime implementation in this review.

## Notes

Full design review: https://github.com/jlevy/metaproc/pull/75#issuecomment-5621778931 . The run-process outer scalar gate also wraps mapped agent leaves submitted to the run-owned pool and records only owner identity; its lease cannot protect a surviving child after owner death. Include parent-death/live-child acceptance in the existing host-safety rollout, alongside fail-closed admission, coherent budgets, pacing, and independent sentinel coverage.
