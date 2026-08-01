---
type: is
id: is-01kyz2ccj23h0sqskdvty6c2gb
title: Detect vendored Metaproc changes in cloud preflight
kind: bug
status: closed
priority: 1
version: 4
labels:
  - cloud
  - correctness
dependencies: []
created_at: 2026-08-01T16:26:42.113Z
updated_at: 2026-08-01T16:46:15.315Z
closed_at: 2026-08-01T16:46:15.314Z
close_reason: "Implemented consumer-layout-aware Metaproc source discovery with committed, dirty, and malformed-.gitmodules regression tests; standalone make verify and all PR #5 checks pass."
---
The cloud dispatch preflight hardcodes the legacy metaproc/ path when comparing a consumer branch with its base. Consumers that vendor Metaproc at another git-submodule path can therefore miss committed gitlink changes and dirty submodule work. Make the check consumer-layout-aware without introducing consumer-specific framework coupling; update focused tests and messages.

## Notes

Implemented generic source-path discovery for Metaproc submodules plus the legacy metaproc directory, with focused regression coverage.
