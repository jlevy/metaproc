---
type: is
id: is-01m7zxwrxawcm9495dbxkj58ec
title: Resolve templated Pi routes before enforcing exact model selection
kind: bug
status: closed
priority: 2
version: 2
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m7zx6xhzepthh19xgavhvjh8
created_at: 2026-11-21T20:48:47.016Z
updated_at: 2026-11-21T21:00:34.020Z
closed_at: 2026-11-21T21:00:34.020Z
close_reason: "Merged in PR #126 at aea8ac8. Exact Gemini profiles, Vertex comparisons, route-safe Pi models, current pricing, pinned reviews and all pre-merge checks passed."
resolution: null
duplicate_of: null
---
Final comparison security review. Preserve a runtime Pi provider template for restricted model IDs and validate the concrete route before starting the CLI, without silently choosing a fallback model.
