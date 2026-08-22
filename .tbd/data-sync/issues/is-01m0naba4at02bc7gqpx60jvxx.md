---
type: is
id: is-01m0naba4at02bc7gqpx60jvxx
title: "PR #25 review R3: document default-ON scalar content retries + opt-outs"
kind: task
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0naatygv870nyw2fvaxje15
created_at: 2026-08-22T18:04:54.794Z
updated_at: 2026-08-22T18:23:17.165Z
closed_at: 2026-08-22T18:23:17.164Z
close_reason: "Fixed in a0c8a0a: §14.1 documents default-ON scalar content retries and opt-outs"
---
Specs with no retry: block now retry content failures on non-fan-out agent steps (cap 3). Document in arch doc §14.1 with opt-outs (on_invalid fail / retry max_retries 0).
