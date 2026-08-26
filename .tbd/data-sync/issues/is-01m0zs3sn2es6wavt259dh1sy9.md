---
type: is
id: is-01m0zs3sn2es6wavt259dh1sy9
title: "PR #49 review H3: bind accepted outputs to the exact plan fingerprint and declared ports"
kind: bug
status: open
priority: 1
version: 1
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
dependencies: []
parent_id: is-01m0zs1svbsptksz66728wzdrb
created_at: 2026-08-26T19:35:21.506Z
updated_at: 2026-08-26T19:35:21.506Z
---
runtime_projection.py currently ignores ResultRecord.step_hash and permits declaration=None, allowing a current PlanBundle to relabel old or unknown outputs. Require an exact current step fingerprint for consumable acceptance and separate or reject undeclared ports.
