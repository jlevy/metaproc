---
type: is
id: is-01m76msm7xe6a3n7dcegpafnrx
title: "Address PR #121 reviews A and B: narrow to reuse_policy never, refuse exact_inputs"
kind: task
status: closed
priority: 1
version: 19
labels: []
dependencies: []
parent_id: is-01m76a30djgz1eds7sfd07wq3b
child_order_hints:
  - is-01m76mtyrpnskkcwhvdds8tvjf
  - is-01m76mv020m6y6dbbvebd4j3jz
  - is-01m76mv18f06hcdxwrqmpdaz35
  - is-01m76mv2gsenavcnxdktcnbvnb
  - is-01m76mv40wfqr7jfs1y034v7w4
  - is-01m76mv57an9rrqya38x2r93xm
  - is-01m76mv6dpcg4ev8dhbdgbf3xn
  - is-01m76mv7h8bgw58e0fkm9c4q7g
  - is-01m76mv8nbbd5z31pan4xjh0h2
  - is-01m76mv9rtp6ta5gwj8cjrtzw1
  - is-01m76mvawmjxkfjam9je57evn1
  - is-01m76mvc1k8ezh4rqxmxtch8k2
  - is-01m76mvd4gj1p95fca1k5jwydx
  - is-01m76mvehxj5kznegaqhhcn00c
  - is-01m76mvfwc54n2sy0ks6507nh3
  - is-01m76mvh5qccsh3vcecewr43th
  - is-01m76mvjraqqq9a9549qrqtewk
created_at: 2026-11-12T01:08:45.948Z
updated_at: 2026-11-12T01:13:58.475Z
closed_at: 2026-11-12T01:13:58.473Z
close_reason: "Every finding of reviews A and B has a disposition; replies posted at https://github.com/jlevy/metaproc/pull/121#issuecomment-6811433380 (A) and https://github.com/jlevy/metaproc/pull/121#issuecomment-6811433681 (B). Head fb335ecaa8f4c8db23208428121e3af7e147b28a, CI run 43132363036 green. Deferred: mp-jft4 (B1), mp-xy7c (A2); design mp-gg7r; test hygiene mp-zdur."
resolution: null
duplicate_of: null
---
Address reviews A (senior) and B (correctness) on PR #121 at head
dee3914e5e001aace3abc728d81b0c6f859845e1.

- A: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
- B: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5867092636

Coordinator decision: narrow the PR to what is clear and safe. Remove the
exact_inputs fingerprinting implementation (findings A1, A3, A4, A5, A9, A10, A14, B2,
B3, B5 resolved by the removal; future design in mp-gg7r), refuse exact_inputs at
planning, keep and finish the `never` work (A6, A7, A8, A11, A12, A13, B4), and defer
the pre-existing bugs on main (B1 as mp-jft4, A2 as mp-xy7c, and the stray
`{{run.dir}}` test directory as mp-zdur).
