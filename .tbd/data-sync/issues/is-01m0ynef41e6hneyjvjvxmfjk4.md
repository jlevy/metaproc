---
type: is
id: is-01m0ynef41e6hneyjvjvxmfjk4
title: Resolve declared input defaults in static process-tree checks
kind: bug
status: closed
priority: 1
version: 2
labels:
  - validation
  - process-spec
dependencies: []
created_at: 2026-08-26T09:12:02.424Z
updated_at: 2026-08-26T09:49:49.539Z
closed_at: 2026-08-26T09:49:49.539Z
close_reason: "Fixed by PR #49 commit 11ea84f: static validation now resolves defaulted process dependencies; make verify and all five GitHub CI jobs pass."
resolution: null
duplicate_of: null
---
check-handlers and check-headers resolve composite dependency paths with an empty variable map, so a dependency path templated only from an optional input's literal default is reported missing even though run-process resolves it correctly. Reuse the runtime input-expansion helper in both static traversals and cover default resolution plus genuinely unresolved placeholders. Keep the implementation consumer-agnostic.
