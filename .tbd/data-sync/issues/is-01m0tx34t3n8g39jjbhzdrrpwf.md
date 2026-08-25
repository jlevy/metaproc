---
type: is
id: is-01m0tx34t3n8g39jjbhzdrrpwf
title: Cut the 0.3.0 release from main
kind: epic
status: closed
priority: 1
version: 13
labels:
  - release
dependencies: []
child_order_hints:
  - is-01m0tx4fmqn5vnwnap35z6wt9s
  - is-01m0tx4wy1bc33ssm10n3a8ap7
  - is-01m0tx6r53cen6nyye2ap03yme
  - is-01m0tx8jrdfkjcr0n1v2b1qs9q
  - is-01m0txe7ccc7y4yhk2wt77d0dj
  - is-01m0txfcvht87nbn777cm2bstv
  - is-01m0txm9k40pwdbx279nqezdy5
  - is-01m0txmwd5ndrr55r28vcnka4w
created_at: 2026-08-24T22:08:42.306Z
updated_at: 2026-08-25T17:01:14.603Z
closed_at: 2026-08-25T17:01:14.602Z
close_reason: Metaproc 0.3.0 was released from the verified stable main line. The larger unmerged runtime work was deliberately excluded for separate review.
resolution: null
duplicate_of: null
---
Ship a minor release covering the changes already merged since the previous release before the larger unmerged mapped-scope runtime work lands as a separately reviewed follow-up. The release captures durable task history, retry feedback, resume correctness, and GCP dispatch work already on main; the unmerged runtime stack is explicitly out of scope.
