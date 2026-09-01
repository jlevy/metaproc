---
type: is
id: is-01m1d4p4nksd76ft31egp4e7sp
title: Restrict raw-path produced refs to upstream dataflow
kind: bug
status: open
priority: 1
version: 1
labels:
  - planning
  - release-blocker
dependencies: []
parent_id: is-01m1d3zgc5kwnxvarym7ebgsyk
created_at: 2026-09-01T00:07:44.562Z
updated_at: 2026-09-01T00:07:44.562Z
---
The planner classifies a raw prompt path as run-produced when any other step declares the same output path, including an independent or downstream step. That can exempt a real authored file from existence and content fingerprinting even though no upstream producer supplies it, allowing stale reuse or a late runtime failure. Derive raw-path produced_refs only from the consumer's transitive dependency ancestors, reject ambiguous producer matches, and add negative coverage for unrelated, downstream, and duplicate producers.
