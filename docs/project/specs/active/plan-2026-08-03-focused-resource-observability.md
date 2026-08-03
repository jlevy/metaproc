---
title: Focused Resource Observability
description: A narrow replacement for pull request 6's resource-accounting work
author: Joshua Levy (github.com/jlevy) with LLM assistance
---
# Focused Resource Observability

**Date:** 2026-08-03

**Author:** Joshua Levy (github.com/jlevy) with LLM assistance

**Status:** Approved

**Implementation:** Complete

## Overview

Metaproc already has a useful first resource-observability plane: normalized resource
events, a hierarchical `resources.json` rollup, a resource-report command, and a
Metabrowser projection.
This plan advances that foundation to the operational contract needed by current
consumers without replaying the breadth of pull request 6.

The replacement is deliberately ledger-first.
Raw evidence is normalized once into a deterministic, reconciled event ledger.
Every persisted rollup, budget result, terminal summary, and recovery path is a
projection of that same ledger.
The design keeps measured, estimated, and unmeasured quantities distinct and never
invents provider requests or actual spend from agent turns or list-price estimates.

## Goals

- Report provider, product, meter, and unit quantities with explicit coverage state.
- Preserve one canonical tool invocation identity so tool totals and budgets cannot
  double-count aggregate and granular evidence.
- Correct agent token and cost normalization, including cache semantics, nested Claude
  usage, Gemini disjoint token buckets, and estimated CLI costs.
- Snapshot resource topology and authored budgets at run creation, then evaluate only
  that immutable snapshot.
- Produce resource artifacts on every terminal path without changing the run’s original
  success or failure outcome.
- Recover artifacts for inactive interrupted runs entirely from local evidence and the
  immutable snapshot, without provider calls or stale cached rollups.
- Add a compact, self-describing `resource-usage-summary.md` whose structured values
  live in validated YAML frontmatter.
- Retain strict read compatibility for `metaproc.resources/v1` while writing the new
  `metaproc.resources/v2` contract.

## Non-Goals

- Compact typed IDs, rerun IDs, or submodule pinning; pull request 9 owns that work.
- Capturing an agent’s final assistant response as a process output.
- Gemini tool-policy or allowlist configuration.
- GCP job-name, log-watermark, or remote-command ergonomics.
- Process-plan fingerprints, plan relocation, or general run-layout migration.
- Provider billing authentication, auth-route inference, or invoice reconciliation.
- Renaming `resources.json` or `.logs/resource-events.jsonl`.
- Calling a provider or cloud API while reporting or recovering resources.
- Refusing dispatch or terminating a run because a resource budget was exceeded.
- Replacing the existing `ResourceEventSource` plug-in with a second provider-specific
  extension system.

## Background

Pull request 6 mixed resource accounting with several unrelated platform changes.
Its resource work also accumulated correctness defects during review: list estimates
were presented as actual cost, cached input tokens could be priced twice, nested Claude
usage was omitted from resource totals, tool invocations could be counted twice, zero
measured values could become unknown, timeout state was inferred from unrelated
historical failures, mutable plan budgets could replace the launch-time contract, and
recovery could reuse a stale `resources.json` instead of replaying the event ledger.

A downstream operational audit over 56 real runs validated the narrower need.
Existing V1 artifacts already answer wall time, token, list-cost, and many tool
questions. The missing contract is provider meters, trustworthy coverage, immutable
reporting budgets, and durable terminal finalization.
Provider request counts and actual cost must remain explicitly unmeasured when exact
evidence is absent.

### Pull request 6 disposition

| Area | Decision | Focused replacement |
| --- | --- | --- |
| Provider meters | Redesign | Exact meter keys and coverage on the shared event ledger |
| Resource budgets | Redesign | Immutable reporting-only snapshots with exact metric targets |
| Terminal finalizer | Redesign | Explicit caller outcome; reporting errors are additive |
| Recovery | Redesign | Replay local ledger for inactive runs; never trust cached totals |
| Tool invocation IDs | Keep, simplify | Producer ID first; deterministic source-local ordinal fallback |
| Agent usage fixes | Keep | Shared normalizers and disjoint token/cost semantics |
| Markdown summary | Keep, harden | Fully self-describing SoftSchema artifact |
| Billing sidecars | Exclude | External authoritative events remain the actual-cost boundary |
| Typed IDs | Exclude | Already delivered by pull request 9 |
| Agent-response capture | Exclude | Independent process-output concern |
| Gemini/GCP/fingerprint changes | Exclude | Independent operational concerns |

## Design

### One normalization and projection pipeline

The canonical data flow is:

```text
source logs and external ResourceEvents
  -> normalized LogEvents
  -> reconciled ResourceEvent ledger
  -> resources.json + resource-usage-summary.md + CLI/browser projections
```

Shared facts are parsed once.
Trace rendering and resource reporting may expose different views, but neither may
maintain a parallel parser for tool calls, tokens, or provider meters.
External integrations continue to emit typed `ResourceEvent` records through
`ResourceEventSource`.

The reconciler applies these rules:

- A producer event ID is authoritative when present.
- Derived event IDs use stable evidence fields and never file mtime.
- Evidence without a timestamp uses a fixed sentinel rather than wall-clock time.
- Byte-equivalent duplicate IDs deduplicate; conflicting duplicate IDs fail loudly.
- Unresolved ownership is assigned either to an `unattributed` node or to the run root,
  never both.
- Every document projection starts from reconciled events, including recovery.

### V2 event and rollup contract

`metaproc.resources/v2` extends the current contract without weakening validation.
Readers dispatch strictly on the schema token and retain a dedicated V1 model.

Each event may carry:

- a stable `event_id`;
- a provider reference (`provider`, `product`, and optional `model`/`request_id`);
- zero or more exact meter observations; and
- lineage identifying the source facts used to derive an estimate.

Meter identity is the tuple `(provider, product, meter, unit)`. A metered quantity has
one coverage state:

- `measured`: exact observed quantity, including measured zero;
- `estimated`: derived quantity with lineage; or
- `unmeasured`: the metric is relevant but exact evidence is absent.

Measured and estimated quantities are distinct fields and never overlap.
Rollups merge only identical meter keys.
Common counters include API requests, failures, retries, cache hits, and cache misses,
while arbitrary provider meters remain representable.

Nodes gain self and total meter rollups alongside V1-compatible scalar metrics.
The document gains terminal outcome, budget evaluations, coverage gaps, and finalization
metadata. New writers emit V2; readers continue to accept strict V1 documents.

### Canonical tool invocations

Normalized log events preserve `tool_name` and `invocation_id` from each adapter:

- Claude `id` and `tool_use_id`;
- Gemini `tool_id`;
- Pi `executionId`; and
- Codex item IDs.

One `ToolSpan` represents one invocation.
Starts and results pair by invocation ID before any fallback.
Records without an ID use a stable ordinal scoped to the source log and tool name;
missing timestamps do not affect identity.
A terminal aggregate tool count contributes only positive residual beyond unique spans.
It is never added wholesale to granular spans.

### Agent usage and cost semantics

Agent adapters normalize whole-attempt usage into disjoint token buckets:

- Codex cached input is a subset of input and is subtracted from the ordinary input
  price bucket before cache pricing.
- Claude totals include nested/subagent `modelUsage`; resource and trace projections
  consume the same normalized value.
- Gemini cached input is separated from uncached input, and any reasoning residual is
  included in billed output exactly once.
- Zero supplied by a provider remains measured zero.

Agent-CLI dollar values and local price-table calculations are `list_cost_usd`
estimates. They never populate `actual_cost_usd`. Actual cost enters only through an
external provider-authoritative ResourceEvent.
Pi costs are explicitly estimated.

Built-in meters report only what the log format proves:

- one measured agent invocation per terminal attempt boundary;
- measured Claude and Codex turns when exact events are present;
- Gemini turns as unmeasured when only aggregates exist;
- measured Claude server-side web-search and web-fetch requests when explicit; and
- provider or LLM request counts as unmeasured unless the source has an exact request
  boundary.

Agent turns, steps, and retries are never aliases for API request count.

### Immutable launch snapshot

At first run creation, `.state/run-config.yaml` receives one `resources` block with a
versioned hierarchy skeleton and normalized budget specifications.
The skeleton is built from the full recursive plan bundle before work starts.
Item and tool leaves may be materialized later from event ownership, but process and
step ancestry is immutable.

Resume reads the existing block and never overwrites it.
A legacy run with no snapshot may still produce a root/unattributed ledger projection,
but it has no authored budgets; the finalizer must not substitute values from the
current process spec.
This preserves the launch-time contract even if source files change or disappear.

### Reporting-only budgets

Budgets target either one scalar metric or one exact meter key.
Supported scopes are run, process/step, provider/product, model, and tool.
Each budget defines a threshold, unit, near-threshold ratio, and posture metadata.
Evaluation produces one of `within`, `near`, `exceeded`, or `unmeasured`.

Existing step `token_budget` values project to total tokens.
Existing `max_budget_usd` values project to `list_cost_usd`, never actual cost.
New explicit resource budgets are additive.
This change reports budget state but does not alter dispatch or termination behavior.

### Causal terminal state and recovery

The run command passes the terminal cause directly to the finalizer:

- normal return -> `completed`;
- propagated timeout exceptions -> `timed_out`;
- cancellation or interrupt exceptions -> `cancelled`;
- other propagated failures -> `failed`.

An item-level timeout remains evidence about that item; it cannot relabel a later run
failure. Historical pool status and error-string scans are not terminal-state inputs.

Finalization runs before the orchestrator lease is released.
It replays sources and the event ledger, writes artifacts atomically, and never replaces
the original process exception.
A finalizer failure is logged as a secondary observability failure.

`metaproc status` may recover missing or stale resource artifacts only when the
orchestrator is inactive.
Recovery uses the immutable snapshot and local source/event files, performs no provider
call, and rebuilds all totals.
It never copies cached values from an older `resources.json`.

### Human and machine summary

The finalizer writes `resource-usage-summary.md`. Its YAML frontmatter contains every
machine-consumed value under one `resource_usage` envelope and the complete SoftSchema
self-description quartet:

```yaml
softschema:
  contract: metaproc.resources:ResourceUsageSummary/v1
  schema: .state/schemas/resource-usage-summary.v1.schema.yaml
  envelope: resource_usage
  status: enforced
```

The relative schema path is resolved from the run directory.
The body is a compact reader-facing explanation and table; no consumer parses it.
The Pydantic source model is compiled deterministically, the compiled schema is
committed as a package resource, and tests validate generated summaries through the
SoftSchema validation API.

### Public and file interfaces

- `read_resources_document(path)` accepts strict V1 or V2 by schema token.
- `reconcile_resource_events(...)` returns the canonical deduplicated ledger.
- `project_resource_document(snapshot, events, outcome, budgets)` is the only totals
  builder used by terminal finalization and recovery.
- `finalize_run_resources(run_dir, outcome=..., trigger=...)` is local-only and
  idempotent.
- `resource-usage-summary.md` joins the existing `resources.json` and
  `.logs/resource-events.jsonl` artifact pair.
- `metaproc resource-report` displays actual cost and estimated list cost separately,
  plus provider meters, coverage gaps, budgets, and terminal outcome.

## Implementation Plan

### Phase 1: Canonical evidence and accounting contracts

- [x] Add strict V1/V2 resource models, exact meters, stable event identities, terminal
  outcomes, and budget result models.
- [x] Normalize tool invocation identities and reconcile aggregate/granular counts.
- [x] Correct shared agent usage, token, and list-cost normalization.
- [x] Make the reconciled ledger the sole source of hierarchical and taxonomy totals.
- [x] Add provider meter extraction with explicit measured/estimated/unmeasured state.

### Phase 2: Durable operational reporting

- [x] Snapshot recursive topology and normalized budgets on first run creation.
- [x] Evaluate snapshot budgets without changing execution behavior.
- [x] Finalize artifacts causally on success, failure, timeout, and cancellation.
- [x] Recover inactive runs by replaying local evidence and the ledger.
- [x] Emit and validate the self-describing resource usage summary.
- [x] Extend the resource-report CLI, browser projection, docs, and compatibility tests.

## File Map

Core changes are expected in:

- `src/metaproc/models/resources.py` for V1/V2 persisted contracts;
- `src/metaproc/logutil/usage.py`, `resource_event_extract.py`, and `tool_spans.py` for
  one normalized evidence path;
- `src/metaproc/engine/resource_rollup.py` for reconciliation and projection;
- small focused modules under `src/metaproc/engine/` for reconciliation, snapshots,
  terminal finalization, and summary rendering;
- `src/metaproc/commands/run_process.py`, `status.py`, and `resource_report.py` for
  lifecycle and operator integration;
- `src/metaproc/engine/resource_summary.py`, its typed model, and a package schema
  resource for the Markdown summary; and
- focused tests matching each contract boundary, plus end-to-end terminal/recovery
  coverage.

No dependency or lockfile change is expected.

## Testing Strategy

Development follows red-green-refactor at each boundary.
Required focused cases include:

- strict V1 and V2 parsing, unknown schema rejection, and measured-zero preservation;
- stable IDs across mtime changes and repeated extraction;
- duplicate deduplication and conflicting-duplicate rejection;
- adapter-specific tool pairing, missing-ID fallback, unmatched calls, and residual
  aggregate counts;
- Codex cache, Claude nested usage, Gemini disjoint buckets, and Pi estimate semantics;
- exact meter-key aggregation and explicit unmeasured request coverage;
- weighted CPU/RSS samples and peak preservation across unequal child sample counts;
- unattributed evidence counted once;
- immutable budget snapshots across a changed process spec and legacy no-snapshot
  behavior;
- completed, failed, timed-out, and cancelled finalization without exception masking;
- inactive recovery rebuilding from changed ledger evidence rather than cached totals;
- CLI and browser recovery refusing to finalize a run while its orchestrator is active;
- summary schema compilation drift and self-describing artifact validation;
- CLI and browser backward compatibility.

The branch must pass focused test modules during implementation, then the repository’s
full `make verify` gate before publication.
A fresh senior engineering review and the pull request’s required CI checks must be
clean before merge readiness is claimed.

## Rollout Plan

New runs write V2 and the summary while existing V1 files remain readable.
Reporting commands label actual, estimated, and unmeasured values explicitly.
Recovery is best-effort only for inactive runs and never changes execution state.
Budget results are informational, so adoption does not introduce a new dispatch failure
mode.

The focused branch replaces pull request 6 with a new pull request.
The old pull request will be closed only after the replacement is published with a
disposition link.

## Open Questions

None. Deferred capabilities are explicit non-goals and should be proposed independently
after this contract has operational evidence.

## References

- [Metaproc pull request 6](https://github.com/jlevy/metaproc/pull/6)
- [Metaproc pull request 9](https://github.com/jlevy/metaproc/pull/9)
- `docs/project/specs/active/plan-2026-05-20-metaproc-resource-usage-and-file-format-policy.md`
- `docs/project/specs/active/plan-2026-08-01-company-product-keyword-breakdowns.md`

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
