---
type: is
id: is-01m260z0rb417gzrk85v4gtcx4
title: "PR 75 F6: serialize stale host-slot reclamation"
kind: bug
status: open
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md
labels: []
dependencies:
  - type: blocks
    target: is-01m1fyjy0qnywd04dw4pm6ejc9
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T16:03:41.962Z
updated_at: 2026-09-10T18:49:55.476Z
---
host_admission.py:257-279 reads a stale lease and later removes the slot path recursively without fencing deletion against replacement. Two reclaimers may observe the same stale lease; A removes/reacquires and B then removes A fresh lease. Atomic mkdir and token checks on owner release do not serialize stale reclamation. Require namespace-level serialization or broker-owned claims and an explicit rollout for old v1 clients, with deterministic two-reclaimer interleaving tests. Coordinate with mp-g3si.

## Notes

The active host-safety plan governs the race and mixed-client closure. mp-3c0g owns broker/owned-launch primitives and mp-c225 platform conformance under Safeproc epic mp-bd6v; mp-g3si now explicitly depends on this finding before Metaproc adoption. Keep this bead actionable for the deterministic old-client reproducer and rollout decision, without waiting to inspect the race until a new package releases. New-client-only locking cannot close mixed-version acceptance. The consolidated execution plan records the exact stale-read/reacquire/delete interleaving.
