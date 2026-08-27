---
type: is
id: is-01m10z8z9aynagg69rfanemcnf
title: Fix the 26 shipped links that escape src/metaproc/docs
kind: task
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:17.002Z
updated_at: 2026-08-27T15:07:49.002Z
closed_at: 2026-08-27T15:07:49.002Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 1, paired with the new shipped-link gate.

Current escaping relative links: metaproc-concepts-and-principles.md 8 total / 6 escaping, metaproc-developer-guide.md 5 / 3, metaproc-operator-reference.md 20 / 17.

Most targets join the shipped set under this plan and become sibling links needing only a path shortening: ../../../docs/arch/arch-metaproc-core.md -> metaproc-design.md, ../../../docs/arch/arch-runpool.md -> arch-runpool.md, ../../../docs/conventions.md -> conventions.md, ../../../docs/artifact-catalog.md -> artifact-catalog.md.

The rest point at things that stay in the repo and need a decision each: ../../../README.md, ../../../AGENTS.md, ../../../examples/offline-smoke/offline-smoke.process.md, ../../../docs/runbooks/, ../../../docs/metaproc-design-rev3-proposals.md. The proposals doc link must be dropped, not converted - it is backlog. For the others, prefer rewriting the sentence over an unchecked absolute URL.
