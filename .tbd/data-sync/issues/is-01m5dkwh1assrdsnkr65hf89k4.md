---
type: is
id: is-01m5dkwh1assrdsnkr65hf89k4
title: "PR #108 F6: migrate the remaining adapter-type comparisons at call sites to adapter-declared capabilities"
kind: task
status: open
priority: 3
version: 1
labels: []
dependencies: []
parent_id: is-01m5dkv38535m7wq6pae32v5kc
created_at: 2026-10-20T21:36:09.766Z
updated_at: 2026-10-20T21:36:09.766Z
---
jlevy on PR #108: "Whatever this is shoudl be driven by configs attached to the adapter (e.g. gemini) not buried in auth code. Apply this prinicple generally." PR #108 established the mechanism (the launch contract, documented in \`metaproc help developer\` under "Launch and Cancellation Contracts" plus a "Where New Things Go" row) and removed the three launcher branches. The legacy branches below were inventoried during the review and left untouched, since migrating them is a much larger change than the PR.

Probe/auth config shaping:
- \`src/metaproc/commands/probe_tool_use.py:100-117\`, the \`_build_probe_config\` if/elif chain on harness name.
- \`src/metaproc/commands/auth_check.py:366-394\` (pi provider inference, claude output_format/verbose, gemini assert_model stream-json, codex permission default), \`:425\` (pi \`--no-tools\`), \`:434\` (pi GCP token injection), \`:453\` (pi registration gate), \`:483\` (pi JSONL error scan), \`:621\` \`_SERVED_MODEL_READERS\` keyed by adapter name.

Candidate: an optional \`ProbeConfigurable\` / live-probe-defaults protocol on the adapter plus a served-model reader hook, probed through \`adapters/registry.py\`.

Other sites: \`run_parallel.py:860\`, \`run_process.py:4889\`, \`auth.py:378\`, \`dispatch/pool_dispatch.py:234-236\`, \`dispatch/preflight.py:349\`.

Not branches, listed to pre-empt false positives: \`run_parallel.py:425,1498,1662\`, \`run_process.py:3059\`, \`build_plan.py:297,334\` compare two adapter variables rather than a literal.

The log-filter selector (\`adapter_type == "pi-cli"\`) is tracked separately in F3, since it carries a launch bug as well.
