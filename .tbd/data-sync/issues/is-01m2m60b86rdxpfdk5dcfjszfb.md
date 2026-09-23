---
type: is
id: is-01m2m60b86rdxpfdk5dcfjszfb
title: "PR #83: read AgentOperationsSummary/v1 documents written before sample_source and RetryStepRow.process"
kind: bug
status: closed
priority: 1
version: 4
labels: []
dependencies: []
created_at: 2026-09-16T04:01:10.405Z
updated_at: 2026-09-16T04:16:31.070Z
---
Filed from PR #83 (https://github.com/jlevy/metaproc/pull/83), after a downstream consumer's CI caught it. Settles the decision `mp-n9j5` asked for.

## What broke

The review fixes on `run-operations-summary` added two fields to `metaproc.operations:AgentOperationsSummary/v1` without moving the contract id:

- `sample_source` on `PoolRow` and `ParallelismFigures` (P1-R8). Both derive from `_Explained`, whose validator refuses a null with no reason in `unavailable`, so a document that predates the field fails on its default.
- `process` on each `retries.by_step` row (P1-R7), required with no default, so the same document fails again. It is `required` in the compiled JSON Schema too, which breaks any reader in any language, not only the Pydantic one.

Reproduced on two real published run trees written by a 0.4.1-era build:

    parallelism.pools.0: Value error, sample_source is null without a reason in unavailable
    retries.by_step.0.process: Field required

`read_operations_summary` returned `None` for an unreadable document and for an absent one alike, so the downstream weekly operations report recomputed the run from its tree, lost `run.process`, and resolved the run kind from the launch label instead.

## The rule chosen

A contract id promises readability: a document declaring `/v1` validates under every later reader of `/v1`. So a field added under an id already in use is optional on read. It needs a default and no validator may make its absence an error. A nullable field qualifies, provided null is a state a reader can act on (the writer did not record it) rather than a value that reads as measured. A newly required field, or a field whose meaning moves, is a new contract id.

`_Explained` therefore applies its explained-null rule to the fields a document states (`model_fields_set`) rather than the fields the current model declares. The fold names every figure it builds, so the writer obligation is unchanged; only the reader stops holding an older document to a field list it never saw.

`extractor_version` counts what the fold measures and emits. It says which fields to expect, never whether a document can be read.

## Changes

- `_Explained._nulls_are_explained` binds stated fields; `RetryStepRow.process` is nullable, and rows without one group by `step_id` alone.
- `read_operations_summary` returns `OperationsSummaryRead` (absent / unreadable / read), logging an unreadable document with its path and error. `operations rollup` reports such a run as `summary_source: unreadable`. `status` reads no summary and cannot fail on one; finalization is still fully guarded.
- Regression tests over a checked-in real pre-change document, read through both the Python model and the shipped JSON Schema.
- `docs/project/specs/active/plan-2026-09-11-agent-operations-summary.md` and `CHANGELOG.md` record the rule.

## Audit of the rest of #83

`ResourceUsageSummary.unpriced_models` and `ResourcesDocument.unpriced_models` (P1-R1) are defaulted lists that no validator requires, so a v0.4.1 document validates unchanged; confirmed against a real one, which the test now pins. `HostAdmissionDeniedEvent` is a new event type, and `ProcessCompleteEvent.errors` is a defaulted dict. No other model under `src/metaproc/models/` changed since v0.4.1. `AgentOperationsSummary/v1` was the only break.
