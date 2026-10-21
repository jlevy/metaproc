---
type: is
id: is-01m5dkwge5z0b0k3jgdhy6wa3a
title: "PR #108 F5: run-step --wait leaves a step record in running after the caller dies by a signal"
kind: bug
status: open
priority: 3
version: 1
labels: []
dependencies: []
parent_id: is-01m5dkv38535m7wq6pae32v5kc
created_at: 2026-10-20T21:36:09.156Z
updated_at: 2026-10-20T21:36:09.156Z
---
\`run-step --wait\` catches only \`TimeoutExpired\`. After a SIGTERM or SIGHUP the step state record stays \`running\`, as it did before PR #108 (the PR changed whether the agent tree survives, not what the state record says).

PR #108 added a launcher-internal \`_CallerSignal\` that is raised while the signal is being converted into group cleanup. Catching it in \`run_step\` to \`mark_failed_at(...)\` before the signal is re-delivered would require making that exception public. Re-delivery kills the caller before its own \`finally\` blocks run (e.g. \`auth_check\`s \`prompt_path.unlink\`), which is also pre-PR behavior for these signals.
