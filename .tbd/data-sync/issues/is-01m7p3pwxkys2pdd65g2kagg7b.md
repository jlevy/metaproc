---
type: is
id: is-01m7p3pwxkys2pdd65g2kagg7b
title: "November model and harness refresh: Claude Code, Codex, Gemini CLI, Pi pins and catalog"
kind: task
status: in_progress
priority: 1
version: 15
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
child_order_hints:
  - is-01m7p8qg31xk7q5jxat8g8vdm1
  - is-01m7pcs9cdxj63cstt09t9xh2d
  - is-01m7pdp3jhnj93fcrxmr8p3b5x
  - is-01m7zp4cbar3p40p3v5j1p0c7s
  - is-01m7zrwcx5bcasfhh022k56eey
  - is-01m7zrwfck199qwzsftbp6q7pe
  - is-01m7zrwh11k1fkfehpvhnd9pxc
  - is-01m7zrwjt4apz7f8bvq3w3cv0z
  - is-01m7zrwn5bhx3j35r4mxm5sv43
  - is-01m7zrwpv45cthbty1p1g7v2xw
  - is-01m7zrwrh1atzkefm9e3n2v0ge
  - is-01m7zs5r5rs7pq7khhxz2k9jt6
  - is-01m7zx6xhzepthh19xgavhvjh8
created_at: 2026-11-18T01:18:01.650Z
updated_at: 2026-11-21T20:36:50.875Z
---
Upgrade the four pinned agent CLIs to the newest releases outside the 14-day cool-off (Claude Code 2.1.318, Codex 0.166.0, Gemini CLI 0.66.0, Pi 1.5.0), refresh the model catalog, Pi packaged models, and pricing against current provider evidence, and preserve existing behavior: no silent default moves, retained IDs kept with lifecycle labels. Covers mp-xgrd, mp-7471, mp-z4rh, mp-4w8q and the pricing part of mp-jnws where evidence allows.

## Notes

Implemented on PR jlevy/metaproc#126; Fable 5.2 technical review findings H1-H3 and M1-M5 fixed; make verify green at da5ab9d. Close on merge.
