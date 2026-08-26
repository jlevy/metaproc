---
type: is
id: is-01m0yzw76xsjbg8wfcanrhqst3
title: Transport Gemini prompts outside ignored audit-log paths
kind: bug
status: in_progress
priority: 0
version: 2
labels:
  - adapter
  - gemini
dependencies: []
created_at: 2026-08-26T12:14:18.844Z
updated_at: 2026-08-26T12:14:24.060Z
---
Gemini CLI can refuse a prompt-file reference when the prompt is stored under an ignored runtime log directory. Preserve the auditable prompt copy, but transport the executable prompt through a supported boundary that does not depend on the agent reading an ignored path. Add a provider-free regression that exercises the constructed command and filesystem lifecycle, then run focused adapter tests and the repository verification gate.
