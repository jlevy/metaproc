---
type: is
id: is-01m0nab9cd278ttbnyx2dm7r20
title: "PR #25 review R1: YAML repair resolves paths unlike the validator"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m0naatygv870nyw2fvaxje15
created_at: 2026-08-22T18:04:54.029Z
updated_at: 2026-08-22T18:23:16.492Z
closed_at: 2026-08-22T18:23:16.492Z
close_reason: "Fixed in a0c8a0a: shared repair_declared_outputs in engine/validation.py, used by both agent call sites"
---
run_process.py:1336-1340 and run_parallel.py:2141-2145 join the unrendered template basename to the first output's parent; validation renders templates and resolves absolute/multi-part paths (validation.py:299, 240-244). Repair silently never fires for templated basenames, probes wrong dir for multi-dir outputs, and can hand a directory to repair_frontmatter_file (IsADirectoryError). Fix: shared repair_declared_outputs helper in engine/validation.py used by both agent call sites.
