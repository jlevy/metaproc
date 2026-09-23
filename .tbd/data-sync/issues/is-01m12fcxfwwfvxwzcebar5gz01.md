---
type: is
id: is-01m12fcxfwwfvxwzcebar5gz01
title: Regroup the developer guide as a user doc, not a contributor doc
kind: task
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
created_at: 2026-08-27T20:43:17.883Z
updated_at: 2026-08-27T20:48:35.874Z
closed_at: 2026-08-27T20:48:35.874Z
close_reason: Fixed. Developer guide returns to Essential Docs beside the operator reference, split stated by task. Architecture is its own contributor section, anchors repointed. The guide's Purpose line, which caused the miscategorization, now names workflow developers. make verify green.
resolution: null
duplicate_of: null
---
The Metaproc Developer Guide is for people building workflows ON Metaproc, alongside the operator reference for running them. Both are user docs. I grouped it with the architecture docs, which was wrong.

Root cause: the guide's Purpose line reads 'For engineers extending metaproc or building a workflow on top of it', and I followed 'extending metaproc'. The rest of the doc is workflow-author material: process specs over orchestrators, wrapper antipatterns, fan-out vocabulary explicitly 'not part of metaproc itself', and a client worked example.

Steps:
1. README: Developer Guide returns to Essential Docs beside the Operator Reference, with the split stated (operator = running existing workflows; developer = building new ones). Architecture becomes its own section again.
2. Repoint the two inbound anchors back to README#architecture (docs/development.md, docs/project/README.md).
3. Tighten the guide's Purpose line so it does not read as a contributor doc.
4. make verify, commit, push, update the spec Outcome.
