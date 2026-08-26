---
type: is
id: is-01m0xta1b0xhmyzthty3hn0rhf
title: "PR #48: make combined GCP auth propagation test hermetic"
kind: bug
status: closed
priority: 0
version: 4
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels: []
dependencies:
  - type: blocks
    target: is-01m0xrg6jeywxa1hwns3eay01m
parent_id: is-01m0xrg4vr6n4znzxz0kkxxxt7
created_at: 2026-08-26T01:17:45.696Z
updated_at: 2026-08-26T01:18:51.039Z
closed_at: 2026-08-26T01:18:51.038Z
close_reason: Made the combined auth/secret-hydration regression hermetic, supplied the prerequisite service-account contract, asserted the generated secret reference, and passed all 17 focused label-propagation tests plus Ruff checks.
resolution: null
duplicate_of: null
---
Update the auth-policy orchestrator propagation regression for the settled secret-hydration prerequisite: provide a synthetic service account, isolate the pool-user environment, and prove both auth flags and secret-reference requirements without inheriting developer state.

## Notes

Full make verify exposed the single failure: the consolidated auth propagation test omitted the prerequisite service-account contract and inherited the caller pool identity. Fix is test-only and will assert the combined boundary explicitly.
