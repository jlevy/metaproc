---
type: is
id: is-01m0t4v9e0tas8gh2t745exy3z
title: "M0 vertical slice: offline three-item mapped composite"
kind: task
status: closed
priority: 0
version: 11
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies:
  - type: blocks
    target: is-01m0xrg6jeywxa1hwns3eay01m
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T15:04:59.071Z
updated_at: 2026-08-26T01:50:36.302Z
closed_at: 2026-08-26T01:50:36.301Z
close_reason: Framework verification and the private exact-pin three-item consumer M0 both pass.
resolution: null
duplicate_of: null
---
Run the minimum in-process composite for_each path as a deterministic three-item child process through existing run_fan_out machinery and explicit output declarations. Prove one parent run, no child Metaproc CLI or lease, isolated item failure, failed-item-only child resume, and current plan/status/trace visibility before using provider credentials.

## Notes

Exact-pin consumer M0 passed against public framework head f94b8a98d87bc588b8434214658b94ab3b18f689. The deterministic three-item parent completed, isolated one injected item failure, blocked only its descendants, resumed only the failed item while retaining siblings, rejected changed immutable inputs, preserved current status and exact source identity, acquired only parent leases, and launched no child Metaproc CLI. Consumer identifiers and artifacts remain recorded only in the private repository.
