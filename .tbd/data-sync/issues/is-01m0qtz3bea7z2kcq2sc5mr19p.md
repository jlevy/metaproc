---
type: is
id: is-01m0qtz3bea7z2kcq2sc5mr19p
title: "PR #28 review R1: replace source-string checks with resume behavior"
kind: bug
status: closed
priority: 2
version: 3
labels:
  - code-review
dependencies: []
parent_id: is-01m0qtyxs4b0jp21d7nh07zskn
created_at: 2026-08-23T17:33:49.293Z
updated_at: 2026-08-23T18:02:52.515Z
closed_at: 2026-08-23T18:02:52.515Z
close_reason: Replaced source inspection with a red-green CLI resume regression that restores one incomplete middle task and proves completed neighbors are reused.
---
R1 (Medium) from PR #28 review. tests/test_item_aligned_chains.py:121 reads run_process.py as text instead of exercising resume behavior. Replace it with a focused CLI or orchestration-level regression that runs an item-aligned chain, makes a later member actionable while the head remains complete, resumes without --force, asserts the missing member executes and restores its status/output, and asserts completed per-item stages are reused. Review: https://github.com/jlevy/metaproc/pull/28#issuecomment-5387403763
