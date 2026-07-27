---
type: is
id: is-01kygat035xcheze599f3yxqrb
title: Complete standalone Metaproc extraction
kind: epic
status: open
priority: 1
version: 14
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
created_at: 2026-07-26T23:05:20.229Z
updated_at: 2026-07-27T16:26:30.623Z
---
Deliver the complete clean-history extraction, standalone package, validation, branch publication, and downstream submodule pin.

## Notes

Standalone source-preview extraction is published as ready-for-review PR #1 at a89af85c058eebe85b4978df707a52b1cd828b2f. Main is the clean migration base and repository default. Lint, distribution, and Python 3.12/3.13/3.14 CI all pass. A downstream consumer pins the same commit. Remaining scope is the first public v0.1.0 release and immutable-package cutover tracked by mp-0mra.
