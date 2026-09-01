---
type: is
id: is-01m1d4p5b1gsjsa6kbws4hxevw
title: Reconcile post-v0.3.0 release records for a v0.4 candidate
kind: task
status: open
priority: 1
version: 2
labels:
  - release
  - supply-chain
  - release-blocker
dependencies: []
parent_id: is-01m1d3zgc5kwnxvarym7ebgsyk
created_at: 2026-09-01T00:07:45.248Z
updated_at: 2026-09-01T01:15:54.189Z
---
The release-facing records disagree with the current candidate. CHANGELOG still describes the SoftSchema 0.7 range and omits later user-visible fixes, while pyproject requires 0.8; the audited first-party exception policy still records 0.7 although uv.toml grants 0.8, contrary to the policy's version-change rule; the roadmap and active consolidation plan still describe merged work as pending; and several superseded review beads remain in progress. Reconcile the aggregate post-release delta, update the audited exception rationale, choose the SemVer minor implied by public removals and contract changes, move the completed plan to done with final evidence, and disposition stale tracking before tagging.

## Notes

Review at main 72ae119: v0.3.0..main is 85 commits touching 272 files (+21,493/-6,392). The first-parent delta contains 13 merged PRs spanning mapped composites/shared admission, runtime projection, cloud security and placement, documentation packaging, SoftSchema 0.8, agent exit/env behavior, raw-path planning, fan-out status totals, and Gemini cwd. Exact-head make verify passed with 4,556 tests and 8 skips. Release records still need reconciliation: CHANGELOG names SoftSchema 0.7 and omits PRs 57/58; SUPPLY-CHAIN-SECURITY records the 0.7 exception while uv.toml/pyproject select 0.8; TODO and the active mapped-runtime plan describe merged work as pending; mp-1af0 and old review tasks still carry pre-merge notes/status. Treat v0.4.0 as the working SemVer candidate because the delta adds mapped composite scope behavior and changes public output/transport contracts, subject to the release review.
