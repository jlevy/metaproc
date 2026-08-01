---
type: is
id: is-01kyx37mj1agq5zha1x5gn574f
title: Upgrade to softschema 0.4 and release
kind: epic
status: open
priority: 1
version: 9
labels: []
dependencies: []
child_order_hints:
  - is-01kyx3858jjt1n5knws87kv64j
  - is-01kyx385g5c392kmb9zga9qhm6
  - is-01kyx385qhs8bt0wzr4r4d25mh
  - is-01kyx385z9ab0pdr0aajs4c32e
  - is-01kyx38gn4gwmp93rst4psbm0x
  - is-01kyxrkfdemk7ev08vch9d6h7p
created_at: 2026-07-31T22:03:06.176Z
updated_at: 2026-08-01T04:36:09.005Z
---
Metaproc now adopts the complete SoftSchema 0.2-0.4 compatibility boundary and frontmatter-format 0.4 behavior on PR #3. Implementation, review hardening, and delivery are tracked under mp-9u9s; this original epic remains open only for the post-merge Metaproc 0.2.0 release.

## Notes

PR #3 contains the complete SoftSchema/frontmatter-format 0.4 upgrade and is green at dfb4deb. Release-preparation PR #4 is green at f72d414, stacked on #3, and tracked by completed bead mp-q0rj. The epic remains open only for mp-yxay: merge #3, retarget and merge #4, then create and verify the v0.2.0 GitHub/PyPI release.
