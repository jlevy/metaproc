---
type: is
id: is-01m0vxbz0kar3cyk8njpkemq1w
title: "PR #37 B7: revalidate child outputs on mapped resume"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-25T07:32:45.715Z
updated_at: 2026-08-25T07:32:45.715Z
---
Resume discovery validates only the mapped parent outputs, not declared child-process outputs. Give this a fixed or explicitly deferred disposition with a repair-path test before the relevant scale rung. Source: PR #37 senior review B7.
