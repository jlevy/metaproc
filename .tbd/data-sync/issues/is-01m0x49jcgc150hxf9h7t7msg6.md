---
type: is
id: is-01m0x49jcgc150hxf9h7t7msg6
title: "R8: always release credential lease when diagnostics fail"
kind: bug
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies: []
parent_id: is-01m0x358va0njc6k4g00pccj7e
created_at: 2026-08-25T18:53:01.710Z
updated_at: 2026-08-25T19:25:27.039Z
closed_at: 2026-08-25T19:25:27.039Z
close_reason: Fixed with unconditional post-acquisition teardown and injected diagnostic-failure coverage.
resolution: null
duplicate_of: null
---
complete_slot guarantees teardown when classification fails, but diagnostic preservation sits outside that guarantee. An unexpected adapter or diagnostics exception can leave the slot directory, active counter, and label lock owned forever. Put all post-acquisition work behind a teardown-on-error boundary and add a focused regression.

## Notes

Fixed: all post-acquisition diagnostic preservation sits behind unconditional slot teardown; regression forces diagnostics failure and proves the slot, counter, and lock are released.
