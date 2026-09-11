---
title: Agent Operations Summary
description: Record what agent steps did as typed events while they run, fold them into a contract-bound summary at finalization, and make run-to-run comparison a diff of two documents instead of a transcript scan.
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
tokens, tool-call count, provider meters. Nothing describes what the agents *did*.

There is no per-step breakdown, no notion of an attempt, no record of which model actually
served an invocation, and the provider meter for `api_requests/count` reports
`unmeasured`. `process-events.jsonl` carries `step_start`, `step_complete`, `item_start`,
`item_complete` and `elapsed_s`, and nothing about attempts, tools or models.

So every question about agent behaviour is answered after the fact, by re-reading
`.logs/tasks/**/*.jsonl`. On a large cohort that is hundreds of megabytes, including
individual transcripts in the tens of megabytes. The scan is slow, it runs when the data is
cold and may already have been pruned, and each new question needs its own throwaway
script. Two runs analysed a week apart are not comparable, because nothing fixes which
questions get asked.

**The run should record this about itself as it happens, and summarise it at
finalization.**

## Motivation, from a recent two-arm comparison

A production comparison of two models over one cohort, run sequentially with a single
variable, needed roughly a day of ad-hoc scripting to answer questions the run already had
the answers to. Four findings came out of it, and none was visible in any emitted artifact:

- **One step retried a third of its invocations, every failure on the same field of the
  same contract.** The cause was recoverable only by pattern-matching the prose of the
  *next* attempt's rendered prompt. A final failed attempt, with no retry after it, leaves
  no structured record of why it failed at all.
- **Three tool results hit the 16 MB result cap**, costing 271.8 s and delivering nothing
  to the model, since a truncated result is discarded rather than summarised. Visible
  nowhere but the raw tool results.
- **A second model billed alongside the step model on 40 invocations.** Visible only in
  `result.stats.models`, and easy to misread as a substitution when it is a grounded tool's
  helper model.
- **Effective concurrency was 9.0 against a DAG ceiling of 17.** The run spent 22,776 s of
  provider time in 2,534 s of wall clock; the other arm saved 10,152 s of provider time and
  719 s of wall clock, so **7% of the saving reached the clock**. That gap is the largest
  single cost in the pipeline and is reported nowhere.

Each of these is generic to any agent pipeline. None of them is a domain question.

## Goals

- Typed events for agent attempts, so the record exists during the run and survives a run
  that fails partway or is resumed.
- One contract-bound summary per run describing agent behaviour, emitted at finalization
  beside `resource-usage-summary.md`.
- A drill-down path from that summary to per-step, per-item and per-invocation detail,
  without re-reading transcripts.
- Run-to-run comparison as a field-wise diff of two summaries.
- Anomalies surfaced by rule with stable codes, so the same condition always surfaces the
  same way and two runs' anomaly lists are comparable.

## Non-Goals

- **Not replacing `ResourceUsageSummary`.** That owns machine resources and cost meters.
  This owns agent behaviour. No field appears in both.
- **Not a new log format.** Transcripts are unchanged. The new events go in
  `process-events.jsonl`, which already exists and is already typed.
- **Not domain reporting.** Whether a step's *output* was any good is a question only the
  downstream project can answer. This describes execution, not judgment.
- **Not retroactive.** Existing runs do not gain one. A post-hoc fold from transcripts is
  possible as a migration path and is deliberately the fallback, not the design.

## Design

### Three new process events

`runpool/process_event_models.py` is already a typed discriminated union with
`extra="forbid"`, and `runpool/process_events.py` already has one logger method per event.
Three additions follow that pattern exactly.

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

class AttemptRejectedEvent(_ItemEventBase):
    event: Literal["attempt_rejected"] = "attempt_rejected"
    attempt: int
    output: str
    contract: str | None
    kind: str            # structural | semantic | unreadable | missing
    invariant: str | None
    location: str | None
    message: str
```

`attempt_rejected` is the one that changes what is knowable. The boundary already computes
this verdict in order to decide whether to retry; today it renders it into the next
attempt's prompt and keeps no structured copy. Emitting it costs nothing and makes the
failure census exact rather than regex-recovered, including for a terminal failure that no
retry follows.

### The summary

`metaproc.operations:AgentOperationsSummary/v1`, envelope `agent_operations`,
`status: enforced`, schema staged to
`.state/schemas/agent-operations-summary.v1.schema.yaml`, written to
`agent-operations-summary.md` at the run root. Frontmatter-md, exactly as
`ResourceUsageSummary` does it: validated YAML for machines, a generated prose body for
humans that states it is explanatory.

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
      search: {repo_wide: {...}, runs_wide: {...}, scoped: {...}}

  tool_use:
    calls_total / exec_s_total
    by_tool: {tool: {calls, seconds}}
    search_reach:
      repo_wide / runs_wide / scoped: {calls, bytes, seconds}
      truncated_at_cap: {count, seconds, cap_bytes}

  retries: [...]                   # one row per attempt_rejected
  anomalies: [...]
```

### Drill-down

```
<run>/
  agent-operations-summary.md            L1  bounded, contract-bound, diffable
  .state/operations/
    steps/<step-id>.yaml                 L2  by step
    items/<item-key>.yaml                L2  by item
    invocations.jsonl                    L3  one flat row per attempt
```

L3 is a projection of the event stream, not a fold of transcripts. L2 and L1 are folds over
L3. A summary answers "how much" and cannot answer "which one"; L3 is what makes the second
question answerable without reopening a transcript.

**Percentiles do not compose.** If L1 and L2 both carry `p50`/`p90`, neither can be a fold
of the other's summary statistics. L3 carries the per-attempt durations, and both L1 and
L2 compute their percentiles from L3 directly rather than from each other.

### Anomalies are rules with stable codes

| Code | Fires when |
| --- | --- |
| `model_substituted` | a requested model is absent from its served set |
| `search_truncated_at_cap` | a tool result hit the cap, so time was spent and nothing delivered |
| `retry_concentration` | one step, or one contract field, accounts for an outsized share of retries |
| `attempt_wrote_nothing` | an attempt completed without writing its declared output |
| `unscoped_search_volume` | repository-wide search bytes exceed a threshold |
| `concurrency_underrun` | effective concurrency far below the DAG ceiling |
| `auxiliary_model_share` | an auxiliary model exceeds a share of output tokens |

Thresholds are emitter defaults with an optional per-process override, not configuration
nobody sets. Every rule above corresponds to something that actually happened in the
comparison described in Motivation and was found by hand.

### Comparison

```
metaproc operations diff <run-a> <run-b>
```

Walks the contract, reporting ratios on numeric leaves and set differences on categorical
ones. Two assertions run before any ratio is printed, because both were violated by hand-
rolled tooling during the comparison that motivated this:

- **Population equality.** Same steps, same items, same counts. Tooling that named a
  subset of items compared 28 invocations against 161 without saying so.
- **The decomposition identity.** `requests × tokens-per-request × seconds-per-token` must
  reproduce the duration ratio. A mismatch means the populations differ and the comparison
  is meaningless.

### Search-scope classification

`repo_wide` / `runs_wide` / `scoped` grades a shell command by the widest reach of any
recursive search in it. This belongs here rather than downstream: the checkout root and the
runs directory are both metaproc concepts, and "an agent grepped the entire repository and
got back more bytes than it can read" is a framework-level pathology, not a domain one.

## Implementation Plan

### Phase 1: Events

- [ ] Three event models, three union members, three logger methods.
- [ ] Emit `attempt_start` / `attempt_complete` around the adapter invocation.
- [ ] Emit `attempt_rejected` where the output boundary already computes its verdict.
- [ ] Typed-read coverage, so the new fields survive the reader rather than being narrowed
  away, which is the failure mode 0.4.1 already had to fix once for pool events.

### Phase 2: Summary and drill-down

- [ ] `models/agent_operations.py` and `engine/agent_operations.py`, beside their
  resource-summary counterparts.
- [ ] Register the contract in `plugins/registry.py`.
- [ ] Emit on the existing finalization trigger, with the same `finalization` semantics
  including recovery.
- [ ] Compiled schema staged and drift-tested against the model.

### Phase 3: Anomalies, prose, comparison

- [ ] The rule table, each a pure predicate with a stable code and a default threshold.
- [ ] Prose renderer, with a test asserting every number in the body appears in the
  frontmatter.
- [ ] `metaproc operations diff`, including both assertions above.

### Phase 4: Transcript fallback

- [ ] A fold from transcripts for runs predating the events, explicitly the migration path
  rather than the design.

## Testing Strategy

The gate is a **replay against a completed two-arm comparison** whose figures are already
established by hand: provider time, request count, repository-wide search calls and bytes,
truncated-result count and seconds, per-step retry counts and the field each failed on, and
effective concurrency. If the emitter cannot reproduce a number the transcripts
demonstrably contain, it is wrong, and that is checkable against existing published runs
rather than waiting for the next cohort.

Beyond that: a round-trip test that L1 equals the fold of L3, a drift test on the compiled
schema, and one anomaly-rule test per code with a fixture that fires it and one that does
not.

## Rollout Plan

Additive throughout. The events are new lines in an existing log that typed readers already
tolerate growing. The summary appears beside one that already exists, and nothing reads it
until the diff command is wired.

**An observability artifact must never fail a production run.** A failure to emit the
summary is logged and does not fail the run, on the same footing as `ResourceUsageSummary`
today.

## Open Questions

- **Does a composite run emit one summary or several?** A process that spawns child
  processes has a summary-shaped question at each level. One per run root with steps
  attributed to their subgraph is probably right, and `subgraph_key` already exists on
  every process event to support it.
- **Where does L3 live?** `.state/operations/` keeps the run root clean, but a reader
  hydrating a published tree to answer "which attempt" needs it present in the publication.
- **Is `invocations.jsonl` bounded enough?** Roughly 400 rows for a 40-item cohort is
  comfortable; a very wide scan is not, and that is when it should become one file per step.
- **Should `attempt_complete` carry per-tool result bytes, or only counts and seconds?**
  Bytes are what revealed the truncation pathology, but they are also the largest field.

## References

- `docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md`
- `src/metaproc/models/resource_summary.py` and `src/metaproc/engine/resource_summary.py`,
  the pattern this copies
- `src/metaproc/runpool/process_event_models.py`, the union these events join
