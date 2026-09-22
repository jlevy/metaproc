---
type: is
id: is-01m2kjp1t7a750zjdww5e5jj0j
title: Adopt SoftSchema 0.9 validation execution evidence for built-in contracts
kind: task
status: open
priority: 3
version: 1
labels: []
dependencies: []
created_at: 2026-09-15T22:23:30.118Z
updated_at: 2026-09-15T22:23:30.118Z
---
When metaproc moves its softschema pin past 0.8 (pyproject.toml softschema>=0.8.0,<0.9), adopt the 0.9 execution-evidence API for built-in contract reports.

- Unskip the 26 cases in tests/test_builtin_contract_evidence.py gated on StructuralResult.execution (test_builtin_model_evidence_survives_report_serialization and siblings); they assert structural.execution: not_run with skipped_reason: inferred_via_model, semantic.execution: completed on acceptance and rejection, and the document-enforcement-via-model-only advisory.
- Update metaproc-operator-reference.md (Checking Output Contracts, Built-in contract checks) and docs/runbooks/softschema-validation.runbook.md to describe the execution fields (completed / not_run / errored) and the model-only advisory; PR #79 review R8 scoped those sections to 0.8 behavior because 0.8.0 emits neither.
- Record the pin move in CHANGELOG.md and SUPPLY-CHAIN-SECURITY.md per the dependency policy.

Source: https://github.com/jlevy/metaproc/pull/79#issuecomment-5688716379 (R8).
