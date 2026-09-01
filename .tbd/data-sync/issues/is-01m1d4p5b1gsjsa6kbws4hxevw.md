---
type: is
id: is-01m1d4p5b1gsjsa6kbws4hxevw
title: Reconcile post-v0.3.0 release records for a v0.4 candidate
kind: task
status: open
priority: 1
version: 1
labels:
  - release
  - supply-chain
  - release-blocker
dependencies: []
parent_id: is-01m1d3zgc5kwnxvarym7ebgsyk
created_at: 2026-09-01T00:07:45.248Z
updated_at: 2026-09-01T00:07:45.248Z
---
The release-facing records disagree with the current candidate. CHANGELOG still describes the SoftSchema 0.7 range and omits later user-visible fixes, while pyproject requires 0.8; the audited first-party exception policy still records 0.7 although uv.toml grants 0.8, contrary to the policy's version-change rule; the roadmap and active consolidation plan still describe merged work as pending; and several superseded review beads remain in progress. Reconcile the aggregate post-release delta, update the audited exception rationale, choose the SemVer minor implied by public removals and contract changes, move the completed plan to done with final evidence, and disposition stale tracking before tagging.
