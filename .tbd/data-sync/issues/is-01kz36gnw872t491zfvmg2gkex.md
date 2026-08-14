---
type: is
id: is-01kz36gnw872t491zfvmg2gkex
title: "PR #9 review PR9-R5: reject non-integer child ordinals"
kind: bug
status: closed
priority: 3
version: 3
labels:
  - pr-review
  - pr-9
dependencies: []
parent_id: is-01kz36g3q9wbmhwnwcs170y1s3
created_at: 2026-08-03T06:55:54.749Z
updated_at: 2026-08-03T07:04:38.088Z
closed_at: 2026-08-03T07:04:38.087Z
close_reason: "Fixed: derive_typed_child_id now requires exact int ordinals, raises TypeError for bool/float inputs, and preserves ValueError for negative integers. Regression test passes."
---
Formal review PR9-R5 (Low), PR #9. src/metaproc/ids.py:311. derive_typed_child_id accepts floats, so logical ordinal 1 and 1.0 derive different identities. Require exact int type, TypeError for other values, ValueError for negative ints, with regression tests.
