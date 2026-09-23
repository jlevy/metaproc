---
type: is
id: is-01m1fy1b65wadea9vgd4me0zv6
title: Refresh Claude, Codex, and Pi adapter pins
kind: chore
status: closed
priority: 2
version: 3
labels:
  - adapters
  - supply-chain
dependencies: []
created_at: 2026-09-02T02:09:14.948Z
updated_at: 2026-09-02T02:23:48.310Z
closed_at: 2026-09-02T02:23:48.309Z
close_reason: "Implemented and validated in Metaproc PR #68; all five CI jobs passed."
resolution: null
duplicate_of: null
---
Advance Metaproc's exact CLI contracts to the newest releases that clear the 2026-08-18 supply-chain cutoff: Claude Code 2.1.234, Codex 0.147.0, and Pi 0.84.2. Pi also moves from the retired @mariozechner package scope to @earendil-works. Update adapter install hints and focused tests; the downstream trading repository owns package installation, Docker image pins, native-installer review, and memory reprofiling.
