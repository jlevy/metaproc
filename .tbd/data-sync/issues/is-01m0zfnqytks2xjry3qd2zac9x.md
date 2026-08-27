---
type: is
id: is-01m0zfnqytks2xjry3qd2zac9x
title: src/metaproc/runpool/README.md is orphaned from the doc graph
kind: task
status: closed
priority: 3
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:50:23.834Z
updated_at: 2026-08-27T15:07:52.757Z
closed_at: 2026-08-27T15:07:52.756Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Referenced only from tests/test_locking_policy.py, which hardcodes its path. Not linked from README.md, development.md, or even arch-runpool.md. Either link it from arch-runpool.md or fold it in and delete it - but update the test either way.
