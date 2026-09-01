---
type: is
id: is-01m1fjkceg1rn2q9dz9cdx3zts
title: Reject Gemini terminal results that omit the requested model
kind: bug
status: closed
priority: 0
version: 4
labels:
  - adapters
  - gemini
  - correctness
dependencies: []
created_at: 2026-09-01T22:49:23.152Z
updated_at: 2026-09-01T23:06:45.812Z
closed_at: 2026-09-01T23:06:45.811Z
close_reason: "PR #67 merged to main at a75f491663e05aea609c96ccb5412f4f37ca20da after all hosted Python 3.12/3.13/3.14, lint, and distribution checks passed. Standalone exact-head make verify passed 4,587 tests with 8 skipped plus all audits/build/installed-wheel smoke. Formal senior review found no issues."
resolution: null
duplicate_of: null
---
Gemini CLI 0.55.1 can accept -m gemini-3.6-flash while terminal result.stats.models reports only gemini-3.5-flash (google-gemini/gemini-cli#28859). Add an adapter-owned terminal-result validation hook and make scalar and fan-out execution reject successful results whose reported model set omits the requested model. Preserve valid multi-model accounting when the requested model is present, avoid broad exact-version enforcement, and cover both execution paths.

## Notes

Exact commit e7d45ef implements the adapter-owned terminal-result contract. Focused tests: 118 passed. Standalone exact-head make verify (outside the Trading uv workspace): lint/types/docs/browser/supply-chain green; 4,587 passed, 8 skipped; npm and uv audits clean; sdist/wheel build and installed-wheel smoke pass. Ready for upstream PR and hosted CI.
