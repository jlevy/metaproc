---
type: is
id: is-01m5dkwfrzevm5fvhkn36pk721
title: "PR #108 F4: run_sampled_step_command has the same orphan-on-SIGHUP property, and an unbounded drain on cancel"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m5dkv38535m7wq6pae32v5kc
created_at: 2026-10-20T21:36:08.473Z
updated_at: 2026-10-20T21:36:08.473Z
---
PR #108 added \`_caller_signals_end_the_command()\` so a SIGTERM or SIGHUP to Metaproc ends the owned foreground command instead of orphaning its tree. \`run_sampled_step_command\` (code steps under \`run-step\` and \`run-parallel\`) launches with \`start_new_session=True\` too and has the same property for its own callers, but was left out of PR #108 scope because it is pre-existing rather than introduced there. Adopting the same context manager is close to a one-line change plus a test.

Note the fix is a no-op for the run-process path, which installs its own reaper (\`runpool/kill.py:323-328\`, from \`run_process.py:254\`): the new watch deliberately replaces only \`SIG_DFL\` dispositions. It matters for \`run-step\` code steps, which install no handler.

Separately, the cancel path drains with an unbounded \`communicate()\` after \`_terminate_process_tree\` (\`src/metaproc/engine/resource_sampling.py\`, around line 355). The leader-only kill fallback added in PR #108 makes a hang much less likely but does not bound it.
