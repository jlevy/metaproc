---
type: is
id: is-01kyje3v4kk0y9jkxm5yvzjaga
title: "PR #1 review AUTH-05: Avoid process-wide environment replacement races"
kind: bug
status: closed
priority: 3
version: 2
spec_path: docs/project/specs/active/plan-2026-07-26-standalone-extraction.md
labels: []
dependencies: []
parent_id: is-01kyje203wwq9b8jqxgwe7574v
created_at: 2026-07-27T18:41:37.426Z
updated_at: 2026-07-27T19:59:55.065Z
closed_at: 2026-07-27T19:59:55.065Z
close_reason: "Accepted by design after call-path review: these operations run only in synchronous command/probe paths, not concurrently in the asynchronous execution path."
---
PR #1 release-readiness review finding AUTH-05. Scope: Avoid process-wide environment replacement races. Record an explicit fixed, rebutted, or deferred disposition.
