---
type: is
id: is-01m0nab9qtwwdna28tq29937b0
title: "PR #25 review R2: stale retry/yaml_repair docstrings + arch doc retry defaults"
kind: task
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0naatygv870nyw2fvaxje15
created_at: 2026-08-22T18:04:54.393Z
updated_at: 2026-08-22T18:23:16.821Z
closed_at: 2026-08-22T18:23:16.821Z
close_reason: "Fixed in a0c8a0a: retry.py + yaml_repair.py docstrings, arch doc §14.1 table and resolution chain corrected"
---
retry.py:1 says run-parallel only; yaml_repair.py:16-20 says exclusively run_parallel call site; arch-metaproc-core.md §14.1 table says max_retries default 0/mult 2.0/cap 120 vs actual 12/1.5/600 (models/authored.py:335-338) and resolution list says off-by-default.
