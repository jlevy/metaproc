---
type: is
id: is-01m0y8805r37nxmf2cpes9sptx
title: Complete the VizModel projection of authored and resolved process fields
kind: bug
status: closed
priority: 1
version: 3
labels:
  - visualization
  - observability
dependencies: []
created_at: 2026-08-26T05:21:19.026Z
updated_at: 2026-08-26T06:50:10.450Z
closed_at: 2026-08-26T06:50:10.450Z
close_reason: "Fixed in public PR #49 at 46249c1; standalone make verify passed with 4,434 tests and all GitHub CI jobs are green."
resolution: null
duplicate_of: null
---
The recursive visualization contract claims exhaustive step details but drops authored process outputs and material fields from ResolvedStep and FanOut. Add field-parity regressions, expose process outputs, step resources/on_failure/execution_profile/artifact_namespace, and fan-out source/filtered_count/align/max_concurrency through the existing pure projection. Keep this observation-only: no scheduler semantics, persisted run-record authority, or application-specific fields.
