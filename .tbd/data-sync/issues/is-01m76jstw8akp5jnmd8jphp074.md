---
type: is
id: is-01m76jstw8akp5jnmd8jphp074
title: Agent-step tests write a literal {{run.dir}}/.logs tree into the working directory
kind: bug
status: open
priority: 3
version: 1
labels: []
dependencies: []
parent_id: is-01m76a30djgz1eds7sfd07wq3b
created_at: 2026-11-12T00:33:55.591Z
updated_at: 2026-11-12T00:33:55.591Z
---
Found while addressing PR #121: running the test suite from a checkout leaves an untracked
`{{run.dir}}/.logs/tasks/<step>/` tree in the working directory, ignored by `**/.logs/` in
`.gitignore` and so easy to miss.

Identified by running each test alone from an empty tree. These five tests in
`tests/test_run_execution_context.py` write it:

- `test_scalar_agent_steps_share_one_executable_leaf_ceiling` (`left`, `right`)
- `test_scalar_agent_steps_use_one_run_owned_pool` (`left`, `right`)
- `test_run_owned_pool_admits_agent_leaves_to_host_slots_after_pool_admission`
  (`leaf-0` to `leaf-5`)
- `test_agent_leaf_host_limit_precedence_under_a_run_owned_pool` (`leaf`)
- `test_agent_leaf_without_a_run_owned_pool_uses_the_scalar_default` (`leaf`)

Each calls `_execute_agent_step` with `variables={}` and `run_dir=tmp_path / "run"`.
`prepare_step` (`src/metaproc/engine/runtime.py`, `run_dir = compute_logs_dir(spec,
variables).parent`) derives the task logs directory from the variables, not from the
`run_dir` the executor receives, and `compute_run_dir` returns the literal `{{run.dir}}`
when `RUNS_DIR` and `RUN_ID` are absent. Prompts and transcripts therefore land in
`./{{run.dir}}/.logs/tasks/<step>/` under the pytest working directory.

Fix: pass `RUNS_DIR` and `RUN_ID` (or `run.dir`) in those tests' variables so the logs
land under `tmp_path`, and consider having `prepare_step` take the executor's `run_dir`
so a missing variable cannot place logs outside the run. Add an assertion that no
`{{run.dir}}` directory appears in the working directory.
