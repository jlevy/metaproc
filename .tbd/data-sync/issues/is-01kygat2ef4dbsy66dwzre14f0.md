---
type: is
id: is-01kygat2ef4dbsy66dwzre14f0
title: Validate source and wheel distributions end to end
kind: task
status: closed
priority: 1
version: 6
spec_path: docs/project/specs/done/plan-2026-07-26-standalone-extraction.md
labels: []
dependencies:
  - type: blocks
    target: is-01kygat2y7dkk9xqy1n1pqck2v
parent_id: is-01kygat035xcheze599f3yxqrb
created_at: 2026-07-26T23:05:22.638Z
updated_at: 2026-08-09T18:57:01.191Z
closed_at: 2026-07-26T23:39:03.523Z
close_reason: "Exact committed tree passed make verify: 3,772 tests passed, 8 environment-gated tests skipped; Python/JS/Markdown/static/policy/audit gates passed; sdist and wheel contents plus isolated installed-wheel CLI/help/env/skill smoke passed."
---
Inspect built artifacts and smoke-test the installed CLI, data, docs, skill, and Metabrowser plugin from an isolated wheel.
