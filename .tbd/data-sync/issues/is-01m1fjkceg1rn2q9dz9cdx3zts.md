---
type: is
id: is-01m1fjkceg1rn2q9dz9cdx3zts
title: Reject Gemini terminal results that omit the requested model
kind: bug
status: in_progress
priority: 0
version: 3
labels:
  - adapters
  - gemini
  - correctness
dependencies: []
created_at: 2026-09-01T22:49:23.152Z
updated_at: 2026-09-01T23:02:32.082Z
---
Gemini CLI 0.55.1 can accept -m gemini-3.6-flash while terminal result.stats.models reports only gemini-3.5-flash (google-gemini/gemini-cli#28859). Add an adapter-owned terminal-result validation hook and make scalar and fan-out execution reject successful results whose reported model set omits the requested model. Preserve valid multi-model accounting when the requested model is present, avoid broad exact-version enforcement, and cover both execution paths.

## Notes

Exact commit e7d45ef implements the adapter-owned terminal-result contract. Focused tests: 118 passed. Standalone exact-head make verify (outside the Trading uv workspace): lint/types/docs/browser/supply-chain green; 4,587 passed, 8 skipped; npm and uv audits clean; sdist/wheel build and installed-wheel smoke pass. Ready for upstream PR and hosted CI.
