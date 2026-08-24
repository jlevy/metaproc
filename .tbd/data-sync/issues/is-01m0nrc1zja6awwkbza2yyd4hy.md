---
type: is
id: is-01m0nrc1zja6awwkbza2yyd4hy
title: "PR #26 review L3: bypasses the curated metaproc.io surface"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0nrb7r4e9ejhv5yp3q4amm0
created_at: 2026-08-22T22:09:59.282Z
updated_at: 2026-08-22T22:32:56.852Z
closed_at: 2026-08-22T22:32:56.852Z
close_reason: null
---
schema_conform.py:38,41 import new_yaml from frontmatter_format and atomic_output_file from strif; both are re-exported by metaproc.io, whose __all__ is guarded by tests/test_io_init.py. fmf_split_frontmatter is not on the curated surface and should be added.
