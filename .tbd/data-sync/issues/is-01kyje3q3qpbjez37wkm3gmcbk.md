---
type: is
id: is-01kyje3q3qpbjez37wkm3gmcbk
title: "PR #1 review TST-04: Replace timing sleeps with condition polling"
kind: bug
status: deferred
priority: 2
version: 6
spec_path: TODO.md
labels: []
dependencies: []
parent_id: is-01kyje203wwq9b8jqxgwe7574v
created_at: 2026-07-27T18:41:33.303Z
updated_at: 2026-08-16T08:00:28.166Z
extensions:
  linear:
    id: 44b81877-046a-48e8-9407-ebc93f68cd97
    linked_at: 2026-08-16T08:00:28.166Z
---
PR #1 release-readiness review finding TST-04. Scope: Replace timing sleeps with condition polling. Record an explicit fixed, rebutted, or deferred disposition.

## Notes

Deferred with explicit judgment: replacing every deterministic, bounded test sleep is not release-critical. Per-test timeouts remain in place and the complete suite passes. Revisit only if timing flakiness is observed.
