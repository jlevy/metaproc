---
type: is
id: is-01m0zs1t5k1r971gh6fzegkbze
title: "PR #49 review B1: reconstruct the canonical runtime run identity"
kind: bug
status: in_progress
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
dependencies: []
parent_id: is-01m0zs1svbsptksz66728wzdrb
created_at: 2026-08-26T19:34:16.499Z
updated_at: 2026-08-26T19:35:21.143Z
---
runtime_projection.py currently treats run-config.yaml run_id as the full task run_id. Real run-process persists a run context there while StatusRecord uses <process>/<run-context>, so projection rejects real run trees. Reconstruct the canonical root runtime identity from declared process plus run context and cover it with a real-shape fixture.
