---
title: Contract Failure Primitives
description: Preserve what a validation failure knows, and let a consumer decide what a failure means, without the framework learning any consumer's vocabulary.
date: 2026-08-20
status: Draft
---
# Feature: Contract Failure Primitives

**Date:** 2026-08-20

**Status:** Draft

## Overview

Metaproc validates a declared output against its contract and marks the step failed. It
then throws away everything it knew about why.

`StatusRecord.error` is a `str`, filled with
`f"output validation failed: {'; '.join(output_errors)}"`. A consumer that wants to know
which output failed, against which contract, or which invariant refused it, has to parse
English. One consumer already does exactly that, with regular expressions, because there
is nothing else available.

This adds two primitives and fixes one bug. It teaches the framework no consumer's
vocabulary.

## Background

### What a consumer had to build

A pipeline needed three things from a finished run: which artifacts were contract-checked,
which invariant refused a failing one, and whether any failure of a kind that should stop
the run had occurred. None was available, so it built a tool that re-walks the finished
tree, maps filenames to contracts from a hand-maintained table, and classifies failures by
pattern-matching error text — `is not of type 'string'` meaning one thing,
`Value error, complete segment shares sum to` meaning another.

Every part of that is a symptom. The table duplicates what the process definitions already
declare. The re-walk exists because nothing aggregates from run state. The regular
expressions exist because the record is prose.

### The bug underneath the largest failure class

That same pipeline measured 504 artifacts against their contracts: 12 failures, of which
**8 were one representation defect** and none was a data problem.

The structural pass validates the *raw parsed document*. The JSON Schema generated from a
pydantic model describes the *serialized* form. A `date` field is `type: string` in the
schema, but YAML parses an unquoted `2026-08-21` into a `datetime.date` before validation
sees it:

```text
as_of_date: "2026-08-21"    str    passes, then the model coerces to date
as_of_date: 2026-08-21      date   fails "not of type 'string'", never reaches the model
```

Both are valid input to the model and mean the same thing. No consumer can fix this except
by asking every author to remember to quote, which YAML does not enforce.

## Design

### The principle this follows

Simple should be simple and complex should be possible. The temptation here is to learn the
consumer's three failure classes — one pipeline calls them *pipeline*, *degradable* and
*authoring* — and give them flags. That would fit one pipeline and misfit the next.

So the framework carries and acts; a consumer names and decides. Metaproc gains no
vocabulary for what a failure *means*. It gains the ability to preserve what it knows, and
to be told what to do about it in terms it already understands, because execution is the
one thing only the framework can do.

A consumer that wants none of this writes nothing and sees no change.

### Primitive 1: the failure record keeps what it knew

`StatusRecord.error` stays, so nothing that reads it breaks. Beside it, a structured list:

```python
class OutputFailure(BaseModel):
    output: str                       # the declared output name
    path: str                         # rendered artifact path
    contract: str | None              # contract id, when one was declared
    kind: Literal["missing", "empty", "structural", "semantic"]
    invariant: str | None             # the validator that refused it
    location: str | None              # path within the document
    message: str                      # human text, unchanged
```

`kind` is the only judgement the framework makes, and it makes it from facts it already
has: the file was absent, the directory was empty, JSON Schema refused it, or a model
validator refused it. Nothing about severity, ownership or what to do.

This is information preservation, not a new concept. `validate_item_outputs` already
distinguishes these cases internally and flattens them into strings on the way out.

### Primitive 2: a consumer maps a failure to an action

A process may declare a mapping from failure to one of the actions the framework can
perform. The actions are a closed set because they are execution outcomes:

| action | meaning |
| --- | --- |
| `fail` | fail the step. Today's behaviour, and the default. |
| `fail_run` | fail the step and stop the run. For failures that recur across every item. |
| `continue` | record the failure and treat the step as complete. |
| `retry` | fail the step and re-run it, subject to the existing retry policy. |

Declared per output, per contract, or per invariant, matched most-specific-first:

```yaml
outputs:
  segment_revenue:
    schema: "trading.v2_google_trends:SegmentRevenue/v1"
    on_failure:
      structural: fail_run        # a representation defect recurs across every ticker
      semantic: retry
```

A consumer that wants richer logic supplies a callable through the plugin registry instead,
receiving the `OutputFailure` and returning an action plus an optional label the framework
stores and never interprets. That label is where a pipeline's own taxonomy lives.

Omitting `on_failure` entirely means `fail`, which is what happens today.

### The bug: normalize representation before the structural pass

Before validating a parsed document against a schema generated from the serialized form,
convert values whose Python type has an unambiguous serialized form — `date`, `datetime`,
`time`, `Decimal`, `UUID` — to that form. One place, every contract, no configuration.

This is correctness, not an extension point. A document and the schema describing it should
not disagree about what a date is.

### What falls out for free

Aggregation and resume need no new primitives once failures are structured. `metaproc
status` can group them by any field, including a consumer's label. "Re-run what failed its
contract" becomes a predicate over run state rather than a new mode.

## Goals

- A consumer can ask which output failed, against which contract, and which invariant
  refused it, without parsing prose.
- A consumer can say that some failures stop a run and others are recorded and stepped
  over, without the framework knowing why.
- A document and the schema describing it agree about representation.
- A consumer that wants none of this is unaffected.

## Non-Goals

- Learning any consumer's failure taxonomy. The framework stores a label; it never reads
  one.
- Repairing artifacts. Correcting a claim an artifact's own data contradicts is domain
  knowledge and stays with the consumer.
- Replacing `StatusRecord.error`. It stays as written so existing readers keep working.

## Implementation Plan

### Phase 1: Preserve the failure, and fix the representation bug

- [ ] Add `OutputFailure` to `src/metaproc/models/runtime.py` and a
      `output_failures: list[OutputFailure]` field on `StatusRecord`, defaulting to empty.
- [ ] Change `validate_item_outputs` in `src/metaproc/engine/validation.py` to return
      `list[OutputFailure]`, with a thin adapter preserving the current `list[str]` for
      existing callers.
- [ ] Normalize `date`, `datetime`, `time`, `Decimal` and `UUID` in the parsed document
      before the structural pass, in `validate_item_outputs`.
- [ ] Carry the structured failures through both `mark_failed_at` call sites in
      `src/metaproc/commands/run_process.py` (around lines 1026 and 1286).
- [ ] Test that a quoted and an unquoted date both validate, and that a genuine type error
      still fails.

### Phase 2: Let a consumer decide what a failure costs

- [ ] Add `on_failure` to `IOSpec` in `src/metaproc/models/authored.py`, keyed by `kind`,
      contract id, or invariant name, with `fail` as the default.
- [ ] Honour it where output validation is checked, including `fail_run`.
- [ ] Allow a plugin to register a classifier receiving an `OutputFailure` and returning
      an action and an optional label; store the label on the record.
- [ ] Group failures by field, including label, in the status surface.
- [ ] Test each action, and that a process declaring nothing behaves exactly as before.

## Testing Strategy

The consumer's corpus is the test set: 504 real artifacts, 12 failures across 5 invariants,
8 of them the date defect. After Phase 1 the 8 pass and the other 4 keep failing, with
`kind` and `invariant` populated. After Phase 2 a process can mark the structural class
`fail_run` and see the run stop.

A process declaring no `on_failure` must produce byte-identical state to today. That is the
regression that matters most, because every existing process is that case.

## Open Questions

- Should `continue` mark the step completed or introduce a third terminal state? Completed
  is simpler and loses the distinction; a new state is honest and touches every consumer of
  `StepStatus`.
- Is `fail_run` reachable cleanly from inside per-item validation, or does it need to
  surface through the coordinator?
- Should the normalization list be fixed or extensible? Fixed is simpler and covers the
  observed defect; extensible invites a consumer to normalize its way out of a real
  disagreement.

## References

- `trading-hzcm` and `docs/project/architecture/arch-contract-failure-handling.md` in the
  consuming repository, which record the workaround this replaces.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
