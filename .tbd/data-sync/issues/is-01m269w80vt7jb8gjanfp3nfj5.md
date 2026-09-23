---
type: is
id: is-01m269w80vt7jb8gjanfp3nfj5
title: Surface actionable failures prominently to the operator agent
kind: feature
status: open
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
child_order_hints:
  - is-01m26a2gvmth1jjgrkg9fs1vha
created_at: 2026-09-10T18:39:28.283Z
updated_at: 2026-09-10T18:42:53.933Z
---
Make every failed, blocked, incomplete, or degraded execution outcome visible and explainable in operator-facing CLI/status output and the operator manual/skill. Surface the primary cause or explicit unknown-cause classification, affected task/scope and attempt, originating stage, retry or admission state, bounded cause counts, and durable evidence references. Preserve causal chains across typed readers and projections without exposing credentials or private prompt data. Never report a plain black-box step failed when underlying evidence exists; keep recovered issues distinct from terminal failure. Define and test the shared operator report contract across scalar, mapped, manual, composite, cancellation, startup/cleanup, missing/corrupt artifact, and partial-run cases.
