---
type: is
id: is-01m260z0rb417gzrk85v4gtcx4
title: "PR 75 F6: serialize stale host-slot reclamation"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T16:03:41.962Z
updated_at: 2026-09-10T16:03:41.962Z
---
host_admission.py:257-279 reads a stale lease and later removes the slot path recursively without fencing deletion against replacement. Two reclaimers may observe the same stale lease; A removes/reacquires and B then removes A fresh lease. Atomic mkdir and token checks on owner release do not serialize stale reclamation. Require namespace-level serialization or broker-owned claims and an explicit rollout for old v1 clients, with deterministic two-reclaimer interleaving tests. Coordinate with mp-g3si.
