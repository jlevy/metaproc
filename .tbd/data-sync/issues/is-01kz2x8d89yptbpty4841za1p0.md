---
type: is
id: is-01kz2x8d89yptbpty4841za1p0
title: "PR #8 review MP8-06: rebuild recovered resources from current ledger"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:06.600Z
updated_at: 2026-08-03T04:20:25.440Z
closed_at: 2026-08-03T04:20:25.440Z
close_reason: Fixed with focused regression coverage; 96 related tests and Python lint/type checks pass.
---
src/metaproc/engine/resource_finalizer.py:193. Provider-free recovery deep-copies stale resources.json while reading a newer event ledger. Rebuild derivable projections from ledger evidence and preserve existing data when no ledger is present; add recovery tests.
