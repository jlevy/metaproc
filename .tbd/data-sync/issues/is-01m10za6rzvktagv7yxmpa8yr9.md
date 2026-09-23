---
type: is
id: is-01m10za6rzvktagv7yxmpa8yr9
title: Resolve the execution-model vs arch-execution topic collision
kind: task
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-27T06:42:57.439Z
updated_at: 2026-08-27T15:07:53.551Z
closed_at: 2026-08-27T15:07:53.551Z
close_reason: Implemented in the documentation reorganization (phases 1-6).
resolution: null
duplicate_of: null
---
Phase 3. The move puts execution-model-design.md (topic 'execution-model', 1,877 words) and arch-execution-model.md (topic 'arch-execution', 2,686 words) in the same directory, in the same topic list, both about the execution model.

They were distinguishable when one sat in docs/ and the other in docs/arch/. In one flat listing they are not.

Decide: merge them, or rename so the split is obvious from the topic name alone. execution-model-design.md is about durable contracts that are expensive to change (semantics versioning, dependency clauses, expansion closure, attempt fencing, admission claims); arch-execution-model.md is the component reference. If that distinction survives, the names should say it.
