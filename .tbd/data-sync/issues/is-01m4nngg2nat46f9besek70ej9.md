---
type: is
id: is-01m4nngg2nat46f9besek70ej9
title: "PR #105 C5: working-directory settings refusal names keys but not the two values"
kind: bug
status: open
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m4nngebtw2grshwdvpd3g8np
created_at: 2026-10-11T14:22:46.356Z
updated_at: 2026-10-11T14:34:06.645Z
---
Low. https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5483638299. gemini_cli.py:421-428. For conservative refusals ($VAR that expands to Metaproc's value, reordered array) the operator sees no reason. Fix: append 'file: <compact JSON> vs Metaproc: <compact JSON>' per key.

## Notes

Deferred from PR #105 (review C, C5). Not a one-line safe fix: the refusal would echo working-directory and Metaproc settings values into an error that lands in run logs and attempt records, and settings such as mcpServers can carry tokens in env or headers. Waits on a decision about which values are safe to echo (redact mcpServers and any secret-bearing subtree, or print only a value's type and length when it differs) and a test for the $VAR and reordered-array refusals the review's probe found.
