---
type: is
id: is-01m5dkvte38ytvp6yb5170755p
title: "PR #108 F3: pi-cli log-filter launch paths have no group cleanup and no kill on timeout"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m5dkv38535m7wq6pae32v5kc
created_at: 2026-10-20T21:35:46.625Z
updated_at: 2026-10-20T21:35:46.625Z
---
The pi-cli log-filter branch launches with a bare \`Popen\`: no new session, no owned-group cleanup, and in \`run_step\` no kill on timeout at all (\`fg_proc.wait(timeout=...)\` just raises, leaving the tree running). This is the one gap the owned-group launch contract documented in PR #108 has to carve out, which undercuts the contract it establishes.

Sites: \`src/metaproc/commands/run_step.py:594\` (the \`use_filter = adapter_type == "pi-cli"\` selector and the Popen below it), \`src/metaproc/engine/runtime.py:268\`, \`src/metaproc/commands/run_process.py:3367\`, \`src/metaproc/commands/run_parallel.py:1588\`.

Left out of PR #108 because a contained fix is not available: it needs a second launcher shape (Popen with the filter thread reading the raw byte pipe, a wait-based timeout loop instead of communicate, group cleanup, thread join) plus the signal watch, applied across four sites.

Candidate: a Popen-based variant of \`run_command_in_process_group\` that takes a stdout consumer, plus an adapter-declared log-filter capability so the \`adapter_type == "pi-cli"\` selector goes away (the general principle jlevy asked for in the PR #108 review).
