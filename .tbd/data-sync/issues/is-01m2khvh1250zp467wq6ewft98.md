---
type: is
id: is-01m2khvh1250zp467wq6ewft98
title: "PR #79 review R8: operator docs describe SoftSchema 0.9 execution evidence the pinned 0.8.0 does not emit"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m2khtp39hxxwknrtk7gzqpfs
created_at: 2026-09-15T22:09:00.961Z
updated_at: 2026-09-15T22:56:33.506Z
closed_at: 2026-09-15T22:56:33.503Z
close_reason: "Fixed in cec5aea: operator reference and runbook describe SoftSchema 0.8 reports; test_builtin_model_report_matches_the_pinned_softschema_release; 0.9 gate reason names mp-3pu3."
resolution: null
duplicate_of: null
---
PR #79 (https://github.com/jlevy/metaproc/pull/79#issuecomment-5688716379), Medium. src/metaproc/docs/metaproc-operator-reference.md:690-711 (Built-in contract checks) and docs/runbooks/softschema-validation.runbook.md:38-42 describe structural/semantic execution fields and the document-enforcement-via-model-only advisory; softschema 0.8.0 (pyproject softschema>=0.8.0,<0.9) emits neither. tests/test_builtin_contract_evidence.py:193-196 skip-gates 26 tests with no tracking bead. Fix: make the docs match 0.8.0 and put a tracking bead id in the skip reason.
