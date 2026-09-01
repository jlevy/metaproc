---
type: is
id: is-01m1d4p5b1gsjsa6kbws4hxevw
title: Reconcile post-v0.3.0 release records for a v0.4 candidate
kind: task
status: open
priority: 1
version: 3
labels:
  - release
  - supply-chain
  - release-blocker
dependencies: []
parent_id: is-01m1d3zgc5kwnxvarym7ebgsyk
created_at: 2026-09-01T00:07:45.248Z
updated_at: 2026-09-01T02:00:09.844Z
---
The release-facing records disagree with the current candidate. CHANGELOG still describes the SoftSchema 0.7 range and omits later user-visible fixes, while pyproject requires 0.8; the audited first-party exception policy still records 0.7 although uv.toml grants 0.8, contrary to the policy's version-change rule; the roadmap and active consolidation plan still describe merged work as pending; and several superseded review beads remain in progress. Reconcile the aggregate post-release delta, update the audited exception rationale, choose the SemVer minor implied by public removals and contract changes, move the completed plan to done with final evidence, and disposition stale tracking before tagging.

## Notes

Review at main 72ae119: v0.3.0..main is 85 commits touching 272 files (+21,493/-6,392). The first-parent delta contains 13 merged PRs spanning mapped composites/shared admission, runtime projection, cloud security and placement, documentation packaging, SoftSchema 0.8, agent exit/env behavior, raw-path planning, fan-out status totals, and Gemini cwd. Exact-head make verify passed with 4,556 tests and 8 skips. Release records still need reconciliation: CHANGELOG names SoftSchema 0.7 and omits PRs 57/58; SUPPLY-CHAIN-SECURITY records the 0.7 exception while uv.toml/pyproject select 0.8; TODO and the active mapped-runtime plan describe merged work as pending; mp-1af0 and old review tasks still carry pre-merge notes/status. Treat v0.4.0 as the working SemVer candidate because the delta adds mapped composite scope behavior and changes public output/transport contracts, subject to the release review.

## Independent re-verification, 2026-08-31 (main 72ae119)

Gate evidence at exact head: `make verify` exit 0 — 4,556 passed / 8 skipped; all 8
skips are environment-gated (4 live-GCP smoke, 3 live trace smoke) plus the known
absent `sample_plugin` fixture (mp-vtpx / mp-ugus). npm audit and the locked uv audit
both report 0 vulnerabilities across 106 packages. Public hygiene, local link checks,
shipped-doc link checks, distribution checks, and installed-wheel smoke all passed.
Hosted CI is green on all 13 first-parent merges since v0.3.0; no pull requests open.

All four record-drift claims confirmed still live at this head:

1. CHANGELOG:162 states `require softschema>=0.7.0,<0.8`; pyproject.toml:30 requires
   `softschema>=0.8.0,<0.9`.
2. SUPPLY-CHAIN-SECURITY.md:42 records the audited exception as `softschema==0.7.0`
   with a rationale written against 0.6.0, while uv.toml grants the 0.8.0 exception.
   The audited rationale was never rewritten for 0.8.0, contrary to the policy's own
   version-change rule.
3. CHANGELOG Unreleased omits PRs 53, 55, 56, 57, and 58 — no entry matches agent exit
   fidelity, produced raw-path refs, agent terminal-styling env policy, fan-out plan
   totals, or Gemini working directory.
4. TODO.md lists mapped composite scopes under Active Development gated on mp-nxs9,
   which is closed; the active plan doc is still `status: Draft — Consolidated Review`
   (last_updated 2026-08-26) and describes merged behavior in the future tense.

Stale tracking to disposition: mp-5igv (P0, PR 44 merged), mp-srbl (merged), mp-zwih
(PR 37 closed), mp-gg32 (PR 38 merged), mp-5248 (PR 39 merged), mp-bjrn (PR 19 merged)
all remain in_progress. mp-1af0 notes still describe uncommitted working-tree work that
landed in PR 49.

SemVer confirmed as v0.4.0: the Removed section drops public CLI surface (`gcp remote`,
`gcp remote-run`, `gcp self-install`, `gcp archive`, `status --cloud-runs-dir`,
`validate --cloud-runs-dir`, `pool retry-missing`) and two environment variables.
