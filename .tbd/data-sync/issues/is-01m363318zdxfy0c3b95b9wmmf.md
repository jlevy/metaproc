---
type: is
id: is-01m363318zdxfy0c3b95b9wmmf
title: "on_change: rerun — a changed input invalidates its consumers and continues"
kind: feature
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-09-23T02:56:32.532Z
updated_at: 2026-09-23T02:56:32.532Z
---
The third response to a changed process input, beside record (default) and new_run (implemented as per-scope input bindings in .state/input-bindings.yaml). A rerun input is a reuse input: a change invalidates the tasks that consumed it and their descendants, along the dependency mappings scheduling uses, and the run continues. Today a value bound through with: or read by a code handler at runtime is outside the step fingerprint (engine/dep_state.py:fingerprint_step; tests/test_resume_launch_changes.py::test_a_changed_variable_changes_only_the_fingerprints_that_bind_it) and the operator's remedy is --from <step> --force. Design: make a rerun input a fingerprint input of every task that consumes it (a declared per-task dependency on the input, not the whole variable set), recorded in run-plan.yaml, so a changed value increments those tasks' generations exactly as far as the data reaches; do not ship the enum value until it changes reuse behavior; making it the default for declared inputs is a migration, not a flip. Design note: src/metaproc/docs/execution-model-design.md, 'Inputs: Identity, Reuse, and Evidence'. Origin: the design assessment in the review of PR #96.
