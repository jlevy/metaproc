---
type: is
id: is-01m0yzw76xsjbg8wfcanrhqst3
title: Transport Gemini prompts outside ignored audit-log paths
kind: bug
status: closed
priority: 0
version: 4
labels:
  - adapter
  - gemini
dependencies: []
created_at: 2026-08-26T12:14:18.844Z
updated_at: 2026-08-26T12:21:14.177Z
closed_at: 2026-08-26T12:21:14.177Z
close_reason: "Fixed locally in commit 230d6da: Gemini now receives the durable audit prompt through its supported headless stdin channel. Provider-free regression, 331 focused execution tests, public-hygiene checks, and the full make verify gate passed (4437 passed, 8 environment-dependent skips)."
resolution: null
duplicate_of: null
---
Gemini CLI can refuse a prompt-file reference when the prompt is stored under an ignored runtime log directory. Preserve the auditable prompt copy, but transport the executable prompt through a supported boundary that does not depend on the agent reading an ignored path. Add a provider-free regression that exercises the constructed command and filesystem lifecycle, then run focused adapter tests and the repository verification gate.

## Notes

Root cause confirmed: Gemini CLI treats @path in the prompt as a model-facing file-read request, so workspace ignore rules can reject the audit-log path. The supported headless stdin channel avoids that coupling. Red/green provider-free regression now executes the constructed command against a fake CLI, proves the complete prompt arrives on stdin, proves the Gemini argv contains no prompt path, and proves the durable audit file remains unchanged. Focused adapter and execution tests: 331 passed.
