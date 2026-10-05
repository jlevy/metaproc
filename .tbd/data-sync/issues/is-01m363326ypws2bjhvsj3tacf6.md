---
type: is
id: is-01m363326ypws2bjhvsj3tacf6
title: Pre-check scalar composite child bindings at launch, before the root records changes
kind: task
status: open
priority: 3
version: 1
labels: []
dependencies: []
created_at: 2026-09-23T02:56:33.501Z
updated_at: 2026-09-23T02:56:33.501Z
---
A composite child scope's on_change: new_run bindings are held at scope entry (commands/run_process.py:_enter_scope_input_bindings), which is mid-run: the root has already recorded its own launch-config changes under the lease when the child refuses, and the composite step fails with the refusal. For a scalar composite whose with: bindings resolve from launch variables, the child's values are known at launch, so run-process could compare them with the child scope's input-bindings.yaml before recording anything, and refuse the whole launch as it does for the root. Mapped composites need their roster and nested scopes need recursion, so the scope-entry check stays the authority; the pre-check is a courtesy that must reuse _prepare_composite_scope's resolution rather than duplicate it. Tests: tests/test_resume_launch_changes.py::test_a_child_binding_holds_under_a_parent_value_the_parent_only_records shows the current mid-run refusal.
