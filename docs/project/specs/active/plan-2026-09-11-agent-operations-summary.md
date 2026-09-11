---
title: Agent Operations Summary
description: Emit the trace Metaproc already knows how to extract, fold it into a contract-bound summary at finalization, and make run-to-run comparison a diff of two documents instead of a transcript scan.
author: Joshua Levy (github.com/jlevy) with LLM assistance
date: 2026-09-11
last_updated: 2026-09-11
status: Draft
category: plan
---
# Feature: Agent Operations Summary

**Date:** 2026-09-11

## Overview

A run emits `resource-usage-summary.md` describing what it cost the machine: CPU, RSS,
tokens, tool-call count, provider meters.
Nothing it emits describes what the agents *did*.

The evidence for that is not missing.
`metaproc.trace` already reads agent transcripts into typed spans, and
`TaskAttemptRecord/0.1` already persists one record per attempt including the structured
list of reasons an output was rejected.
What is missing is a trigger and a fold.
The span store is written only when an operator types
`metaproc trace <run-dir> --extract`, and nothing turns either source into a bounded
document that two runs can be compared through.

So questions about agent behaviour still get answered after the fact by re-reading
`.logs/tasks/**/*.jsonl`. On a large cohort that is hundreds of megabytes, including
individual transcripts in the tens of megabytes.
The scan runs when the data is cold and may already have been pruned, and each new
question needs its own throwaway script.
Two runs analysed a week apart are not comparable, because nothing fixes which questions
get asked.

**The run should emit this record about itself, and summarise it at finalization.**

## What Already Exists

- **A typed span store.** `metaproc.trace` defines `TraceEvent` over eleven span kinds
  (`run`, `level`, `step`, `item`, `attempt`, `agent_session`, `tool_call`,
  `subprocess`, `llm_call`, `provider_call`, `internal`), with hierarchy carried by
  `parent_span_id`. It has an extractor per agent log format and one for the Metaproc
  engine, a linker that joins them across sources and propagates severity upward, and
  `aggregate()` over arbitrary group keys.
  `metaproc trace <run-dir> --extract` writes the result to
  `<run>/.logs/derived/trace.jsonl`.
- **Run-to-run comparison.** `metaproc compare-trace` is already a row-per-group by
  column-per-run matrix over that same aggregation, grouping on
  `adapter.type,step.id,tool.family` by default.
- **Per-attempt rejection causes.** `TaskAttemptRecord/0.1` persists to
  `<run>/.state/tasks/<step>/<item>/attempts/<attempt>/attempt.yaml` with `disposition`,
  an optional `failure_class`, and `output_failures`: a list of `OutputFailure` carrying
  `output`, `path`, `contract`, `kind`, `invariant`, `location` and `message`.
- **A finalization trigger.** `finalize_run_resources` runs at the end of a run, again
  on `metaproc status` when the projection is older than its sources, and on recovery.
  It already writes two contract-bound artifacts.

## The Gaps

- **Nothing triggers extraction.** `finalize_run_resources` neither imports nor calls
  `extract_trace` or `write_trace`. Their only call site in the package is
  `commands/trace.py`, under the `--extract` flag.
  The plugin surface has no post-run hook that could stand in.
  A run therefore has no `.logs/derived/` unless a human asked for one, by which time
  the transcripts may be cold or pruned.
- **The rejection causes never reach the trace.** `attempt` spans are built by the four
  agent-log extractors from the transcript path.
  The engine extractor reads `process-events.jsonl`, the runpool step events and
  `result.yaml`, but not `attempt.yaml`. So the one place that already holds a
  structured answer to “why was this retried” is invisible to `trace` and
  `compare-trace`.
- **`TraceEvent` is unversioned.** The string `TraceEvent/0.1` appears in the module
  docstring and in CLI output, but `TraceEvent` is in neither the softschema contract
  registry nor the schema-token registry.
  Nothing validates a trace file against a stated contract, and nothing pins its shape
  across releases.
- **There is no run-level summary.** `metaproc trace --count` prints a by-kind tally to
  stderr. Nothing is written, so there is no artifact to diff, publish, or attach to a
  run.
- **`process-events.jsonl` says nothing about attempts.** It carries process, level,
  step and item events.
  A run that dies before its transcripts are complete leaves no attempt-level record
  from the engine’s own side.

## Motivation

An internal two-arm model comparison over one cohort, run sequentially with a single
variable, needed roughly a day of ad-hoc scripting to answer questions the run already
held the answers to.
Four findings came out of it, and none was visible in any emitted artifact:

- **One step retried a third of its invocations, every failure on the same field of the
  same contract.** The cause was recovered by pattern-matching the prose of the *next*
  attempt’s rendered prompt, even though `attempt.yaml` had it structurally all along.
- **Three tool results hit the result cap**, costing time and delivering nothing to the
  model, since a truncated result is discarded rather than summarised.
- **A second model billed alongside the step model on 40 invocations.** Easy to misread
  as a substitution when it is a grounded tool’s helper model.
- **Effective concurrency was 9.0 against a DAG ceiling of 17**, so most of the provider
  time one arm saved never reached the wall clock.
  That gap is the largest single cost in the pipeline and is reported nowhere.

Each is generic to any agent pipeline.
None is a domain question.

## Goals

- One contract-bound summary per run describing agent behaviour, emitted at finalization
  beside `resource-usage-summary.md`.
- The trace extracted automatically, so the drill-down that already exists is populated
  for every run rather than for the runs somebody remembered to ask about.
- `attempt.yaml` folded into the trace, so rejection causes are queryable rather than
  regex-recovered.
- Run-to-run comparison as a field-wise diff of two summaries, complementing the
  span-level matrix `compare-trace` already produces.
- Anomalies surfaced by rule with stable codes, so the same condition always surfaces
  the same way and two runs’ anomaly lists are comparable.

## Non-Goals

- **Not a new drill-down subsystem.** `metaproc.trace` is the drill-down.
  This plan turns it on and folds it; it does not replace it.
- **Not replacing `ResourceUsageSummary`.** That owns machine resources and cost meters.
  This owns agent behaviour.
  No field appears in both.
- **Not a new log format.** Transcripts are unchanged, and the span store keeps its
  current path and shape.
- **Not domain reporting.** Whether a step’s *output* was any good is a question only
  the downstream project can answer.
  This describes execution, not judgment.
- **Not retroactive.** Existing runs do not gain a summary automatically.
  A post-hoc fold is possible, since `extract_trace` already works against a completed
  run directory.

## Design

### Extraction at Finalization

`finalize_run_resources` calls `extract_trace` and `write_trace` on the same trigger
that writes `resources.json` and `resource-usage-summary.md`, with the same failure
semantics: an observability artifact must never fail a production run.

This is the smallest change with the largest effect, and it is a prerequisite for
everything below. The recovery path matters as much as the terminal one:
`metaproc status` re-finalizes when the projection is older than its sources, so a run
that died before finalization still gets a trace the first time somebody looks at it.

### Attempt Records in the Trace

The Metaproc engine extractor gains a pass over
`.state/tasks/<step>/<item>/attempts/<attempt>/attempt.yaml`, emitting one `attempt`
span per record with `disposition`, `failure_class`, and one attribute set per
`OutputFailure` (`kind`, `contract`, `invariant`, `location`). The linker already joins
`attempt` spans to their `item` parent.

No new event type is needed for this.
An earlier draft of this plan proposed an `attempt_rejected` process event;
`TaskAttemptRecord/0.1` already persists exactly that verdict, including for a terminal
failure with no retry after it.

### Two Live Attempt Events

`runpool/process_event_models.py` is a typed discriminated union with `extra="forbid"`,
and `runpool/process_events.py` has one logger method per event.
Two additions follow that pattern.

```python
class AttemptStartEvent(_ItemEventBase):
    event: Literal["attempt_start"] = "attempt_start"
    attempt: int
    requested_model: str | None
    adapter: str

class AttemptCompleteEvent(_ItemEventBase):
    event: Literal["attempt_complete"] = "attempt_complete"
    attempt: int
    outcome: Literal["completed", "failed", "timeout"]
    served_models: list[str]
    requests: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    provider_s: float
    tool_calls: dict[str, int]
    tool_exec_s: float
    outputs_written: list[str]
```

These overlap the `attempt` spans the agent-log extractors already produce, and the
overlap is the point: the extractors reconstruct an attempt from a transcript, while
these are emitted live by the runner.
They carry what the engine knows and the transcript does not, chiefly the *requested*
model against the served set, and they survive a run that fails partway, is resumed, or
leaves a truncated transcript.

### The Summary

`metaproc.operations:AgentOperationsSummary/v1`, envelope `agent_operations`,
`status: enforced`, schema staged to
`.state/schemas/agent-operations-summary.v1.schema.yaml`, written to
`agent-operations-summary.md` at the run root.
Frontmatter-md, exactly as `ResourceUsageSummary` does it: validated YAML for machines,
a generated prose body for humans that states it is explanatory.

Its numeric leaves are `Quantity`, so a figure the run could not measure says so rather
than arriving as a zero, and a figure that does not apply to a step is distinguishable
from one nobody collected.

```yaml
agent_operations:
  run_id: ...
  generated_at: ...

  identity:
    process / execution_profile / declared_model / adapter
    source_revision: {revision, dirty, moved}

  execution:
    state / started_at / ended_at / wall_s
    levels / steps_total / steps_failed
    items_requested / items_completed
    effective_concurrency          # provider_s / wall_s
    concurrency_ceiling            # what the DAG allows

  provider:
    provider_s / requests / input_tokens / output_tokens / cached_tokens
    generation_s                   # provider_s - tool_exec_s
    by_model: [{model, role: requested|auxiliary, invocations, requests, tokens...}]

  models:
    requested: {model: count}
    served_as_requested: {model: count}
    auxiliary: [{model, count, steps: [...]}]
    substitutions: []              # present and empty is a stated healthy state

  steps:
    - step / invocations / retries / items_covered / wrote
      duration: {p50, p90, max, total}
      requests / output_tokens
      tools: {tool: count}

  tool_use:
    calls_total / exec_s_total
    by_tool: {tool: {calls, seconds}}
    truncated_at_cap: {count, seconds, cap_bytes}

  retries: [...]                   # one row per rejected attempt
  anomalies: [...]
```

**Percentiles do not compose.** If the summary and its per-step rows both carry
`p50`/`p90`, neither can be a fold of the other’s summary statistics.
Both compute theirs from the span store directly.

### Anomalies Are Rules With Stable Codes

| Code | Fires when |
| --- | --- |
| `model_substituted` | a requested model is absent from its served set |
| `result_truncated_at_cap` | a tool result hit the cap, so time was spent and nothing delivered |
| `retry_concentration` | one step, or one contract field, accounts for an outsized share of retries |
| `attempt_wrote_nothing` | an attempt completed without writing its declared output |
| `concurrency_underrun` | effective concurrency far below the DAG ceiling |
| `auxiliary_model_share` | an auxiliary model exceeds a share of output tokens |

Thresholds are emitter defaults with an optional per-process override, not configuration
nobody sets. Every rule corresponds to something that happened in the comparison
described above and was found by hand.

`result_truncated_at_cap` needs one upstream fix first: `tool.result.truncated` is
declared in `TOOL_USAGE_ATTRS` and populated by no extractor.
Its neighbours are in better shape.
`tool.input.command` is populated by the Claude, Codex and Gemini extractors, and
`tool.result.size_bytes` by Claude and Gemini, though only on the non-error branch and
only for a string result, so an errored or structured result carries no size.

### Comparison

```
metaproc operations diff <run-a> <run-b>
```

`compare-trace` compares spans grouped by key.
This compares two summaries field by field, reporting ratios on numeric leaves and set
differences on categorical ones.
Two assertions run before any ratio is printed, because hand-rolled tooling violated
both during the comparison that motivated this:

- **Population equality.** Same steps, same items, same counts.
  Tooling that named a subset of items compared 28 invocations against 161 without
  saying so.
- **The decomposition identity.** `requests × tokens-per-request × seconds-per-token`
  must reproduce the duration ratio.
  A mismatch means the populations differ and the comparison is meaningless.

## Implementation Plan

### Phase 1: Turn On What Exists

- [ ] Call `extract_trace` and `write_trace` from `finalize_run_resources`, guarded so a
  failure is logged and never fails the run.
- [ ] Cover the recovery and `metaproc status` paths, not just terminal finalization.
- [ ] Register a versioned contract for `TraceEvent` so the file it writes has a stated
  shape.

### Phase 2: Close the Evidence Gaps

- [ ] Read `attempt.yaml` in the Metaproc engine extractor and emit `attempt` spans
  carrying `OutputFailure` detail.
- [ ] Populate `tool.result.truncated`, which no extractor sets today.
- [ ] Add `attempt_start` and `attempt_complete` to the process-event union, emit them
  around the adapter invocation, and cover them in the typed reader so the new fields
  are not narrowed away.

### Phase 3: Summary and Comparison

- [ ] `models/agent_operations.py` and `engine/agent_operations.py`, beside their
  resource-summary counterparts.
- [ ] Register the contract in `plugins/registry.py`, with the compiled schema staged
  and drift-tested against the model.
- [ ] The rule table, each a pure predicate with a stable code and a default threshold.
- [ ] Prose renderer, with a test asserting every number in the body appears in the
  frontmatter.
- [ ] `metaproc operations diff`, including both assertions above.

## Testing Strategy

The agent-log fixtures in `tests/fixtures/trace_agents/` are the gate, because they run
in CI and any contributor can reproduce them.
Extend them with an attempt-record fixture and a truncated tool result, then assert that
the summary folded from them reproduces figures computed independently from the same
fixtures.

Beyond that: a round-trip test that the summary equals the fold of the span store, a
drift test on the compiled schema, one anomaly-rule test per code with a fixture that
fires it and one that does not, and a test that a failing extraction inside
`finalize_run_resources` leaves the run’s outcome unchanged.

A replay against a completed multi-arm comparison is useful for calibration, since the
figures there were established by hand.
It is not the gate, because the runs are not public.

## Rollout Plan

Additive throughout.
The events are new lines in an existing log that typed readers already tolerate growing.
The summary appears beside one that already exists, and nothing reads it until the diff
command is wired.

Automatic extraction is the one change with a cost at runtime: it reads every transcript
in the run at finalization.
That cost is bounded by the same data a manual `--extract` already walks, but it now
lands on every run, so it needs a measurement before it ships and a way to decline it
for a run that does not want it.

**An observability artifact must never fail a production run.** A failure to emit is
logged and does not fail the run, on the same footing as `ResourceUsageSummary` today.

## Open Questions

- **What does automatic extraction cost on a large cohort?** Unmeasured, and it gates
  Phase 1. If it is material, extraction moves behind a process-level setting or runs
  per-step as transcripts complete rather than once at the end.
- **Does a composite run emit one summary or several?** A process that spawns child
  processes has a summary-shaped question at each level.
  One per run root with steps attributed to their subgraph is probably right, and
  `subgraph_key` already exists on every process event to support it.
- **Does the span store belong in a published tree?** It lives under `.logs/derived/`. A
  reader hydrating a published run to answer “which attempt” needs it present, and log
  pruning must know not to take it.
- **Should `attempt_complete` carry per-tool result bytes, or only counts and seconds?**
  Bytes are what reveal the truncation pathology, but they are also the largest field.

## References

- `src/metaproc/trace/`, the span store, extractors, linker and aggregation this builds
  on
- `src/metaproc/commands/trace.py` and `src/metaproc/commands/compare_trace.py`, the
  existing query and comparison surface
- `src/metaproc/models/runtime.py`, `TaskAttemptRecord` and `OutputFailure`
- `src/metaproc/models/resource_summary.py` and
  `src/metaproc/engine/resource_summary.py`, the contract-bound summary pattern this
  copies
- `src/metaproc/engine/resource_finalization.py`, the trigger this hangs from
- `src/metaproc/runpool/process_event_models.py`, the union the two new events join
- [Execution Stability, Operator Diagnostics, and Flexibility](plan-2026-09-10-runpool-execution-followups.md)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
