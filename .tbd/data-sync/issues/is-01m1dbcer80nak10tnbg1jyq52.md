---
type: is
id: is-01m1dbcer80nak10tnbg1jyq52
title: Close the v0.4.0 release loose ends
kind: epic
status: open
priority: 1
version: 6
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
created_at: 2026-09-01T02:04:47.228Z
updated_at: 2026-09-01T02:05:20.208Z
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
