---
type: is
id: is-01m7pdp3jhnj93fcrxmr8p3b5x
title: Use recorded namespace defaults in run-parallel and run-step; rebuild removed profiles in composite children
kind: task
status: open
priority: 2
version: 3
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies:
  - type: blocks
    target: is-01m7zqb27v8m9a1zh6a41z9qgs
parent_id: is-01m7p3pwxkys2pdd65g2kagg7b
created_at: 2026-11-18T04:12:21.457Z
updated_at: 2026-11-21T19:30:52.415Z
---
The November refresh made run-process derive artifact namespaces against run-config's namespace_default_models and rebuild removed built-in profiles from REMOVED_EXECUTION_PROFILES. Gaps: run_parallel.py (~680) and run_step.py (~310) still derive namespaces against current defaults; a removed profile named only inside a composite child plan is not rebuilt (only root-plan profiles are recorded) and gets the clearer refusal instead; run-config.yaml does not record --adapter-config, so status --steps shows such runs as stale (pre-existing).

## Notes

Round 1 C2. Canonical local/Filestore run-process and orchestrator-managed workers now carry the recorded launch choice. Remaining callers are standalone run-step, standalone run-parallel, and direct gcp scale --reconcile, whose repair plan still needs shared resume context.
