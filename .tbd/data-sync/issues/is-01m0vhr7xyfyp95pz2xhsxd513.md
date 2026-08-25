---
type: is
id: is-01m0vhr7xyfyp95pz2xhsxd513
title: "PR #35 N6: stop kill sentinel from consuming retry budgets"
kind: bug
status: open
priority: 1
version: 2
labels: []
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0v08wy0cem0nwa7zeejr8qd
created_at: 2026-08-25T04:09:45.149Z
updated_at: 2026-08-25T04:10:15.999Z
---
A submission rejected after RunPool shutdown begins must become terminal cancellation/teardown, not a synthetic retryable failure for every remaining item. Prove the kill sentinel cannot churn through item retry budgets.
