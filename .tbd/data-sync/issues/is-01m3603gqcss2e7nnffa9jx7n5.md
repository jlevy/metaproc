---
type: is
id: is-01m3603gqcss2e7nnffa9jx7n5
title: Design evolving-run provenance and explicit input change policies
kind: feature
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-09-23T02:04:22.633Z
updated_at: 2026-09-23T02:04:22.633Z
---
Follow-up design from review of https://github.com/jlevy/metaproc/pull/96. Separate logical run identity, semantic reuse dependencies, and immutable execution evidence. Consider on_change enum record/rerun/new_run with provenance for every value and narrow opt-in fixed identities; assess migration from current reuse defaults. Link each attempt/result to immutable launch context, resolved scope inputs, actual worker/code evidence and upstream artifact versions. Preserve mixed-version runs; invalidate affected work instead of aborting on provenance changes. Existing gaps include runtime/with inputs and handler code outside fingerprints and mutable config/latest-attempt snapshots. Proposal only: settle contract before implementation; review comment is the detailed design record.
