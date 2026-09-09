---
type: is
id: is-01m1dbcer80nak10tnbg1jyq52
title: Close the v0.4.0 release loose ends
kind: epic
status: closed
priority: 1
version: 9
labels:
  - release
  - release-blocker
dependencies: []
child_order_hints:
  - is-01m1dbd5pmnwbdzqg3tq72ma34
  - is-01m1dbd620eggpqg2tkcqg93g1
  - is-01m1d4p49c2q2396861xn2vyd3
  - is-01m1d4p4nksd76ft31egp4e7sp
  - is-01m1d4p5b1gsjsa6kbws4hxevw
  - is-01m1f8xgv4zycvj17qc7g9x1d0
  - is-01m22c8wn6qjcqg65rs2pr0qrj
created_at: 2026-09-01T02:04:47.228Z
updated_at: 2026-09-09T07:33:48.493Z
closed_at: 2026-09-09T07:33:48.492Z
close_reason: |-
  v0.4.0 published. Tag v0.4.0 points at 2a0aade, the squash merge of PR #73 and the tip of main, so the published wheel and post-release main are the same tree.

  Gate before tagging: make verify on 2a0aade passed lint-check, 4,613 tests with 8 skipped, and build with distribution and installed-wheel smoke. uv audit could not run in this container (the network policy denies api.osv.dev with a CONNECT 403; confirmed a proxy denial, not a finding) and was covered by the publish workflow's own release gate, which ran it with network access. CI green on all five checks for 2a0aade.

  publish.yml run 34324074426 succeeded in 2m12s: release gate, tag/version verification, and PyPI publish.

  Verified after publish. PyPI metadata: version 0.4.0, license AGPL-3.0-or-later, requires-python >=3.12,<4.0, homepage/repository/issues/changelog/documentation links, 3.12/3.13/3.14 classifiers, both wheel (1.72 MB) and sdist (2.58 MB). All four publishing.md step 9 smoke tests pass against the released version. The shipped documentation claim checks out against the actual wheel: metaproc help lists 17 topics and the arch-runpool topic carries the new Gemini-specific scoping.

  One diagnostic worth recording: the first smoke-test attempt failed to resolve metaproc==0.4.0. That was uv's cached index metadata, not a publish defect; uvx --refresh resolved and reported 0.4.0 immediately. The repo's own uv.toml exclude-newer = '14 days' cool-off would also filter a same-day release, so a post-release smoke test needs --no-config or an equivalent override.

  Left open deliberately and disclosed in the release notes: mp-ad60 (bare resume re-enters completed scalar composites), blocked on the mp-5nko design question.
resolution: null
duplicate_of: null
---
Everything standing between main 72ae119 and a tagged v0.4.0, in one place.

The gate itself is already clean: exact-head `make verify` exits 0 with 4,556 passed and
8 environment-gated skips, both audits report zero vulnerabilities across 106 packages,
public hygiene, link, distribution, and installed-wheel checks pass, and hosted CI is
green on all 13 first-parent merges since v0.3.0. Nothing here is a re-architecture.

What remains is two correctness defects in silent paths, a set of release records that
disagree with the tree, first-party dependencies held behind their current releases, and
tracking that no longer describes reality.

Release is v0.4.0, not a patch: the delta removes public CLI surface (`gcp remote`,
`gcp remote-run`, `gcp self-install`, `gcp archive`, `status --cloud-runs-dir`,
`validate --cloud-runs-dir`, `pool retry-missing`) and two environment variables.

Children are independent and can land in any order; the tag waits on all of them.
