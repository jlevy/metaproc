---
type: is
id: is-01m0yzw76xsjbg8wfcanrhqst3
title: Transport Gemini prompts outside ignored audit-log paths
kind: bug
status: in_progress
priority: 0
version: 3
labels:
  - adapter
  - gemini
dependencies: []
created_at: 2026-08-26T12:14:18.844Z
updated_at: 2026-08-26T12:18:57.957Z
---
Gemini CLI can refuse a prompt-file reference when the prompt is stored under an ignored runtime log directory. Preserve the auditable prompt copy, but transport the executable prompt through a supported boundary that does not depend on the agent reading an ignored path. Add a provider-free regression that exercises the constructed command and filesystem lifecycle, then run focused adapter tests and the repository verification gate.

## Notes

Root cause confirmed: Gemini CLI treats @path in the prompt as a model-facing file-read request, so workspace ignore rules can reject the audit-log path. The supported headless stdin channel avoids that coupling. Red/green provider-free regression now executes the constructed command against a fake CLI, proves the complete prompt arrives on stdin, proves the Gemini argv contains no prompt path, and proves the durable audit file remains unchanged. Focused adapter and execution tests: 331 passed.
