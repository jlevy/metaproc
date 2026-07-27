---
type: is
id: is-01kyje3tfag1fyp84phk38f88n
title: "PR #1 review CLI-08: Avoid blocking sleeps in async retry paths"
kind: bug
status: closed
priority: 3
version: 2
spec_path: docs/project/specs/active/plan-2026-07-26-standalone-extraction.md
labels: []
dependencies: []
parent_id: is-01kyje203wwq9b8jqxgwe7574v
created_at: 2026-07-27T18:41:36.745Z
updated_at: 2026-07-27T19:59:55.054Z
closed_at: 2026-07-27T19:59:55.054Z
close_reason: "Accepted by design after call-path review: these operations run only in synchronous command/probe paths, not concurrently in the asynchronous execution path."
---
PR #1 release-readiness review finding CLI-08. Scope: Avoid blocking sleeps in async retry paths. Record an explicit fixed, rebutted, or deferred disposition.
