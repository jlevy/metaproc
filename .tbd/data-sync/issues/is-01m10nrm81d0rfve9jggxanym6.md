---
type: is
id: is-01m10nrm81d0rfve9jggxanym6
title: Register and fail closed on the run-plan snapshot schema
kind: bug
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - review
  - runtime-projection
dependencies: []
parent_id: is-01m10mm4vpgbqgrjqx4dbjee41
created_at: 2026-08-27T03:56:04.224Z
updated_at: 2026-08-27T04:36:02.245Z
closed_at: 2026-08-27T04:36:02.245Z
close_reason: "Fixed in 9d34c1f; full make verify passed with 4,493 tests and GitHub CI completed 5/5 green. Published per-finding dispositions on PR #49."
resolution: null
duplicate_of: null
---
Register the persisted run-plan snapshot token and contract with Metaproc's schema registries, add resolver and round-trip coverage, and reject unsupported schema versions. The narrowed DTO must not embed a permissive full Plan token.
