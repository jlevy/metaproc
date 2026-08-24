---
type: is
id: is-01m0nabrsa1kj51mp0xrrz9f0q
title: Code-mode fan-out runs YAML repair on deterministic outputs (S3)
kind: task
status: open
priority: 3
version: 1
labels: []
dependencies: []
created_at: 2026-08-22T18:05:09.802Z
updated_at: 2026-08-22T18:05:09.802Z
---
PR #25 review S3 (deferred): run_parallel.py:1032-1036 repairs code/command-step outputs, against yaml_repair's stated LLM-only scope. Decide whether to drop repair there so deterministic producers fail loudly.
