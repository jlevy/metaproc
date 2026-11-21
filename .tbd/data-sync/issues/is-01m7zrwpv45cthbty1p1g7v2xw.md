---
type: is
id: is-01m7zrwpv45cthbty1p1g7v2xw
title: Scope operator Pi overlays to Batch consumers
kind: bug
status: open
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m7p3pwxkys2pdd65g2kagg7b
created_at: 2026-11-21T19:21:22.016Z
updated_at: 2026-11-21T19:30:52.991Z
---
PR #126 F2. Avoid reading or transmitting unrelated Pi operator overlays when the job has no Pi consumer. Keep strict secret validation for jobs that use Pi.

## Notes

Round 2 F2. Resolved worker profiles skip the overlay for non-Pi targets when fallback cannot switch providers. Orchestrator submission must keep validating until remote workspace and saved launch choices establish the complete Pi route set. A later protocol can defer overlay resolution without putting plaintext in Batch metadata.
