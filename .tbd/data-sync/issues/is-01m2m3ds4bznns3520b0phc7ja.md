---
type: is
id: is-01m2m3ds4bznns3520b0phc7ja
title: Adding a nullable field to an _Explained model makes earlier AgentOperationsSummary/v1 artifacts unreadable
kind: task
status: open
priority: 3
version: 1
labels: []
dependencies: []
created_at: 2026-09-16T03:16:04.874Z
updated_at: 2026-09-16T03:16:04.874Z
---
Filed from the PR #83 re-review (finding N3, https://github.com/jlevy/metaproc/pull/83#issuecomment-5691478410).

## The problem

Every operations summary record derives from `_Explained` (src/metaproc/models/operations_summary.py:52). Its `_nulls_are_explained` model validator (:62-68) rejects any field that is null without a matching reason in `unavailable`. A field added to such a model after an artifact was written is absent from that artifact, so it validates at its `None` default, carries no `unavailable` reason, and the whole document fails to parse.

PR #83 added `PoolRow.sample_source`. Every operations summary written before it therefore fails `read_operations_summary`, which returns `None`. The contract identity did not move: both documents declare `metaproc.operations:AgentOperationsSummary/v1`. So a reader has no way to tell a genuinely malformed document from one written by an older extractor, and adding any nullable field is a silent read-break for artifacts under the same contract version.

## Why it is latent

`build_operations_rollup` (src/metaproc/engine/operations_rollup.py:38) reads a written summary only when its `extractor_version` equals `OPERATIONS_SUMMARY_EXTRACTOR_VERSION`, and recomputes the fold otherwise. The extractor version moved to 2 in this PR, so every version-1 artifact is recomputed rather than read, and rollup is the only reader. The format is also unreleased, so no compatibility is owed yet. The defect is real but currently unreachable through the CLI: only a direct `read_operations_summary` call on an older artifact hits it.

It stops being latent the moment a second reader appears that does not gate on the extractor version, or a nullable field is added without bumping it.

## Options

1. **A nullable-field policy in the validator.** Skip fields absent from `model_fields_set`, so a field the document never mentioned is not held to the explained-null rule while an explicit `null` still is. Cheapest, and it makes every nullable addition backward-readable. It weakens the invariant: a writer that omits a field it should have explained is no longer caught, so the extractor's own round-trip test would have to carry that check instead.
2. **A contract version bump rule.** Require `AgentOperationsSummary/v1` to move to `/v2` whenever a nullable field is added, so a reader can refuse an older document by identity rather than by a confusing validation error. Keeps the invariant exactly as strict, and makes the break explicit and diagnosable, at the cost of a version bump per field and of readers needing to handle several contract versions.
3. **A reader-side default.** Have `read_operations_summary` fill `unavailable` for any field the document omits, with a reason naming the extractor version that wrote it. Keeps both the invariant and the contract identity, and keeps the fix in one place, but it puts compatibility knowledge in the reader and the reason text is synthesized rather than measured.

A decision should also say what the extractor version means relative to the contract version, since today they can disagree.

## Not urgent

The format is unreleased and the version gate masks it for the only reader. Pick an option before a second reader of `operations-summary.md` lands, or before the contract is depended on outside this repo.
