---
type: is
id: is-01kygat035xcheze599f3yxqrb
title: Complete standalone Metaproc extraction
kind: epic
status: open
priority: 1
version: 18
spec_path: docs/project/specs/active/plan-2026-07-26-standalone-extraction.md
labels: []
dependencies: []
child_order_hints:
  - is-01kygat0p95ryvtmvd8z8mh8v9
  - is-01kygat156w2kzea4y1vctxk7v
  - is-01kygat1jn1nvazvzf68hjmcgc
  - is-01kygat210x1te3q0xa7vfrgm6
  - is-01kygat2ef4dbsy66dwzre14f0
  - is-01kygat2y7dkk9xqy1n1pqck2v
  - is-01kygat3hyshavp24hvpazztrr
  - is-01kygat425c758vpvw64cwr5jv
  - is-01kyh26keebb99grmnv3szck5f
  - is-01kyj68ywbgzvtcxxgt3qr5zgz
  - is-01kyje203wwq9b8jqxgwe7574v
  - is-01kyjjswdamtc9d94pwbfqn79r
created_at: 2026-07-26T23:05:20.229Z
updated_at: 2026-07-27T20:19:41.052Z
---
Deliver the complete clean-history extraction, standalone package, validation, branch publication, and downstream submodule pin.

## Notes

Standalone final review commit 00d0f14 is pushed on PR #1. The original 100 findings plus one follow-up finding are fully dispositioned (89 fixed, 11 accepted by design, 1 deferred). Local verification passes 3,783 Metaproc tests plus downstream integration suites; standalone lint, distribution, Cursor review, and Python 3.12/3.13/3.14 CI pass. Downstream commit 7ad0233c4 merges current upstream main and pins the exact final revision. First v0.1.0 publication remains tracked by mp-0mra; cross-repository CI access while private is tracked by mp-o07k.
