---
title: Contract Failure Primitives
description: Stop flattening what output validation already knows, and let a process declare what a validation failure costs, without the framework learning any domain's vocabulary.
date: 2026-08-20
status: Draft
---
# Feature: Contract Failure Primitives

**Date:** 2026-08-20

**Status:** Draft

## Overview

Output validation produces structured facts and then destroys them.
`validate_artifact` returns a record naming the failing field, the validator that
refused it, and the value it saw.
Metaproc keeps the first such record, formats it into a sentence, joins it with
semicolons, and stores the result in `StatusRecord.error`, a `str`.

Then it reads that sentence back.
`classify_error` decides whether a contract failure is worth retrying by testing whether
the words `schema`, `envelope`, or `mismatch` appear in it.

The framework is parsing its own English.
Everything downstream inherits that channel: a consumer wanting to know which output
failed or which invariant refused it has no alternative but the same substring matching,
and at least one has written it.

This preserves the record instead, makes the retry rule declarative, and fixes a
representation bug underneath the largest observed failure class.
It teaches the framework no domain’s vocabulary.

## Background

### The Round Trip Through Prose

The path a contract failure takes today, in four steps:

1. `validate_artifact` returns `ArtifactValidationResult`, whose `structural.errors` are
   records like
   `{"kind": "schema_violation", "path": ["earnings_date"], "validator": "type", "validator_value": "string", "message": "...", "value": "2026-08-21"}`.
2. `_format_artifact_validation_error` keeps `errors[0]`, discards the rest, and renders
   `f"{contract_id}: {kind}: {message}"`. Two copies of this function exist, in
   `engine/validation.py` and `commands/validate.py`.
3. The strings are joined into `output validation failed: ...` and stored on
   `StatusRecord.error`.
4. `classify_error` substring-matches that string to choose `RETRY` or `FAIL`, per rule
   4 of the retry priority chain.

Step 4 is where the cost becomes concrete rather than aesthetic, because the substring
test reads the whole sentence and the sentence contains the artifact’s filename.

Two declared outputs of one real process, each missing for the same transient reason, an
agent killed before it wrote its file:

```text
output validation failed: company-research-schema-manifest.md: file not found   -> FAIL
output validation failed: source-snapshot.md: file not found                    -> RETRY
```

Identical failures, opposite verdicts, because one filename contains `schema`. The
comment above the rule states the intent exactly, that missing files are transient and
worth retrying while structural mismatches are not, and the implementation cannot honour
it, because by the time it runs, the fact that distinguishes the two cases is gone.

Substring matching is the right tool one layer down and the docstring in
`engine/retry.py` says why: a subprocess is opaque, its exit code is always 1, so the
error string is the only signal there is.
Contract validation is the opposite case.
The framework holds the structured record and chooses to flatten it.

### What a Consumer Had to Build

A pipeline needed three things from a finished run: which artifacts were
contract-checked, which invariant refused a failing one, and whether any failure of a
kind that should stop the run had occurred.
None was reachable, so it built a tool that re-walks the finished tree, maps filenames
to contracts from a hand-maintained table, and classifies failures by pattern-matching
error text.

The design tests in
[process-framework-concepts.md](../../../process-framework-concepts.md) name this
outcome:

> A workflow forced to answer “no” by building its own coordinator on top of the
> framework is the signal that the framework, not the workflow, needs the change.

Each part of that tool is a symptom.
The table duplicates what the process spec already declares.
The re-walk exists because nothing aggregates from run state.
The regular expressions exist because the record is prose.

### The Bug Underneath the Largest Failure Class

One pipeline measured 504 artifacts against their contracts and found 12 failures, of
which **8 were one representation defect** and none was a data problem.

The structural pass validates the parsed document.
The JSON Schema compiled from a pydantic model describes the serialized form.
A `date` field is `type: string` in the schema, but YAML parses an unquoted `2026-08-21`
into a `datetime.date` before validation sees it.
Reproduced against a registered contract, same field, same value, two representations:

```text
record: {earnings_date: "2026-08-21"}   no structural error
record: {earnings_date: 2026-08-21}     schema_violation, validator "type":
                                        value datetime.date(2026, 8, 21) is not of type 'string'
```

Both are valid input to the model and mean the same thing.
No author can fix this except by remembering to quote, which YAML does not enforce.
Across the 40 contracts registered in that environment, 24 fields are `date` or
`datetime` typed, and 39 of the 40 run a real structural pass rather than skipping it,
so the exposure is broad rather than incidental.

## Design

### Where the Layer Boundary Falls

`arch-metaproc-core.md` §13 already draws it: QA is a domain concern, and check
taxonomies, severity models, and report formats stay in the domain layer.
Nothing here disturbs that.

The clause worth stating explicitly, because it is what makes this proposal compatible
with §13 rather than an exception to it:

> **The framework owns what a failure does to execution.
> The domain owns what a failure means.**

Execution outcomes are the framework’s business because only the framework can perform
them. Severity, ownership, and taxonomy are the domain’s, and the framework should be
unable to read them even when it stores them.

The temptation is to learn one consumer’s three failure classes and give them flags.
That fits one pipeline and misfits the next.

### Primitive 1: Keep the Record

`StatusRecord.error` stays as written, so every existing reader keeps working.
Beside it, a structured list:

```python
class OutputFailure(BaseModel):
    output: str                       # the declared output name
    path: str                         # rendered artifact path
    contract: str | None              # contract id, when one was declared
    kind: Literal["missing", "empty", "unreadable", "structural", "semantic"]
    invariant: str | None             # the validator that refused it
    location: str | None              # path within the document
    message: str                      # human text, unchanged
```

This is not a new vocabulary.
`invariant`, `location`, and `message` are softschema’s `validator`, `path`, and
`message` carried through unchanged.
`kind` subdivides an existing category rather than adding one:
`FailureClass.INVALID_OUTPUT` already aggregates into `FailureCounts` and surfaces in
`pool status`, as a single undifferentiated bucket.
These are the distinctions `validate_item_outputs` already draws internally and discards
on the way out, plus `unreadable`, which softschema reports as `outcome: "input_error"`
and metaproc currently does not distinguish.

The framework makes no judgement beyond `kind`, and makes that one from facts it already
has.

### Primitive 2: Make the Existing Retry Rule Declarative

Rule 4 of the retry chain is already a policy about contract failures.
It is hardcoded, and it is expressed as a substring test over a sentence.
With structured failures it can be expressed over the facts, and a process can state its
own:

| action | meaning |
| --- | --- |
| `fail` | fail the step. Today’s behaviour, and the default. |
| `retry` | fail the step and re-run it, subject to the existing retry policy. |
| `fail_run` | fail the step and stop the run. For failures that recur across every item. |

Two of the three are `RetryVerdict` today.
Only `fail_run` is new, and the concepts doc already contemplates it: an option
governing whether a run aborts on failure may change the run’s verdict, provided it
never changes whether a dependency counts as satisfied.

The field is `on_invalid`, not `on_failure`, because a step already has an `on_failure`
and it answers a different question: whether *this* step runs when an *upstream* step
failed. That is the consumer’s side of an edge.
This is what a step’s own output failing its contract costs.
Two policies, two owners, two names.

Declared per output, keyed by kind, contract, or invariant, matched most-specific-first:

```yaml
outputs:
  segment_summary:
    schema: "example:SegmentSummary/v1"
    on_invalid:
      missing: retry              # the agent died before writing; another attempt may work
      structural: fail_run        # a representation defect recurs across every item
```

A process that wants richer logic registers a classifier through the existing plugin
registry, receiving the `OutputFailure` and returning an action plus an optional label
the framework stores and never reads.
That label is where a domain’s taxonomy lives, on the domain’s side of §13’s boundary.

Omitting `on_invalid` preserves rule 4’s current behaviour, expressed over `kind`
instead of substrings, which also repairs the filename sensitivity shown above.

### What This Deliberately Does Not Add

**No `continue` action.** “Record the failure and treat the step as complete” looks like
a fourth action and is not, because it answers a consumer’s question on the producer’s
behalf. Whether a failed upstream task blocks a downstream one is the **requirement**
axis of a dependency clause, and the concepts doc is explicit that requiring success and
requiring completion are different, legitimate statements that belong to the edge.
Putting it on the producing output forces one answer for every consumer of that output.

Metaproc already has a coarse form of that axis.
A step’s `on_failure: continue` runs that step even when an upstream one failed, and
`propagate_failure` honours it by excluding it from the blocked set.
What is missing is per-edge granularity, cardinality, and outcome descriptors for the
consumer, which is design test 5. Adding `continue` to the producer would duplicate an
existing mechanism at the wrong grain instead of fixing its grain.

**No handling for domain verdicts.** An artifact whose own data contradicts a claim it
makes is not a contract failure to be stepped over.
The concepts doc settles the case directly: domain verdicts, such as an item
legitimately having no answer, are successful outputs, not failures.
The answer is contract design, giving the shape an explicit representation for a refusal
or an empty result, so the verdict propagates as data and stays visible in aggregation.
That is available today and needs nothing from this proposal.

### The Bug: Normalize Representation Before the Structural Pass

Before validating a parsed document against a schema compiled from the serialized form,
convert values whose Python type has an unambiguous serialized form, meaning `date`,
`datetime`, `time`, `Decimal`, and `UUID`, to that form.
One place, every contract, no configuration.

This is correctness, not an extension point.
A document and the schema describing it should not disagree about what a date is.

### What Falls Out

Aggregation already exists and gains resolution: `FailureCounts` can subdivide
`invalid_output` by `kind` without a new surface.
“Re-run what failed its contract” becomes a predicate over run state rather than a new
mode. Neither needs a primitive.

## Goals

- A consumer can ask which output failed, against which contract, and which invariant
  refused it, without parsing prose.
- Two identical failures receive the same retry verdict regardless of the artifact’s
  filename.
- A process can declare that some contract failures stop a run, without the framework
  knowing why.
- A document and the schema describing it agree about representation.
- A process that declares nothing is unaffected.

## Non-Goals

- Learning any domain’s failure taxonomy.
  The framework stores a label and never reads one.
- Repairing artifacts.
  Correcting a claim an artifact’s own data contradicts is domain knowledge and stays
  with the domain.
- Replacing `StatusRecord.error`. It stays as written so existing readers keep working.
- Fan-in requirement policy.
  It is the right home for tolerating failures and it is a separate change.

## Implementation Plan

### Phase 1: Preserve the Failure, and Fix the Representation Bug

- [ ] Add `OutputFailure` to `src/metaproc/models/runtime.py` and an
  `output_failures: list[OutputFailure]` field on `StatusRecord`, defaulting to empty.
- [ ] Change `validate_item_outputs` in `src/metaproc/engine/validation.py` to return
  `list[OutputFailure]`, with a thin adapter preserving the current `list[str]` for
  existing callers. Keep every error, not `errors[0]`.
- [ ] Collapse the two copies of `_format_artifact_validation_error` into the adapter.
- [ ] Normalize `date`, `datetime`, `time`, `Decimal`, and `UUID` in the parsed document
  before the structural pass.
- [ ] Carry the structured failures through both `mark_failed_at` call sites in
  `src/metaproc/commands/run_process.py`.
- [ ] Re-express retry rule 4 over `kind` rather than substrings, preserving its
  documented intent, and test that a missing output retries whatever its filename
  contains.
- [ ] Test that a quoted and an unquoted date both validate, and that a genuine type
  error still fails.

### Phase 2: Let a Process Declare What a Failure Costs

- [ ] Add `on_invalid` to `IOSpec` in `src/metaproc/models/authored.py`, keyed by
  `kind`, contract id, or invariant name, defaulting to rule 4’s behaviour.
- [ ] Honour it where output validation is checked, including `fail_run`.
- [ ] Allow a plugin to register a classifier receiving an `OutputFailure` and returning
  an action and an optional label; store the label on the record.
- [ ] Subdivide `invalid_output` in `FailureCounts` by `kind`, and group by label where
  one is present.
- [ ] Test each action, and that a process declaring nothing behaves exactly as before.

## Testing Strategy

A consumer’s corpus is the reference set: 504 real artifacts, 12 failures across 5
invariants, 8 of them the date defect.
After Phase 1 the 8 pass, the other 4 keep failing with `kind` and `invariant`
populated, and the missing-output retry verdict no longer depends on the filename.
After Phase 2 a process can mark the structural class `fail_run` and see the run stop.

A process declaring no `on_invalid` must produce byte-identical state to today, except
for the two retry verdicts that rule 4 was already getting wrong.
That is the regression that matters most, because every existing process is that case.

## Open Questions

- Is `fail_run` reachable cleanly from inside per-item validation, or does it need to
  surface through the coordinator?
- Should the normalization list be fixed or extensible?
  Fixed is simpler and covers the observed defect; extensible invites a domain to
  normalize its way out of a real disagreement.
- Should `skipped_reason` be preserved too?
  Both softschema passes report why they were skipped, which is what distinguishes a
  contract that is enforced from one that is merely declared.
  A consumer has already rebuilt this by inspecting the registry, which is the same
  signal as the tool described above.

## References

- [process-framework-concepts.md](../../../process-framework-concepts.md), for the
  requirement axis, the two-axis failure model, and the design tests cited here.
- [arch-metaproc-core.md](../../../arch/arch-metaproc-core.md) §13 for the layer
  boundary and §14.1 for the retry chain this makes declarative.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
