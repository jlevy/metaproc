# Resource Roll-Up

Metaproc records operational evidence once and projects it into a reviewable artifact
trio:

- `.logs/resource-events.jsonl` is the typed, append-oriented evidence stream.
- `resources.json` is the deterministic hierarchical projection used by
  `metaproc resource-report` and the Metabrowser resource view.
- `resource-usage-summary.md` is a SoftSchema-style frontmatter plus Markdown review
  projection of the same events, provider meters, coverage, and budgets.

The current machine schema is `metaproc.resources/v2`. The explicit
`ResourcesDocumentV1` model and `read_resources_document_json` reader keep historical
`metaproc.resources/v1` files readable without allowing V2 fields into the strict V1
contract.

## Hierarchy and counters

Every event resolves to the deepest available node in the shared run -> process -> step
-> item -> file -> tool hierarchy.
A node retains `self_metrics` separately from bottom-up `total_metrics`. Null means that
the emitter supplied no evidence; numeric zero means an authoritative measured zero.

V2 adds common counters for API requests, API failures, retries, cache hits, and cache
misses. A top-level tool call is not automatically an API request.
Emit an API counter only at a boundary that knows a provider operation occurred.

## Provider meters

A `MeterKey` is exactly `(provider, product, meter, unit)`. Aggregation occurs only when
all four components match.
For example, SerpAPI Google Trends requests and credits are different rows even if one
call normally consumes one credit.

Each event-level `MeteredQuantity` has one coverage state:

- `measured` populates only `actual_quantity`, including an actual value of zero;
- `estimated` populates only `estimated_quantity`; and
- `unmeasured` populates neither numeric field and records a coverage gap.

`MeterRollup` retains actual and estimated subtotals separately, the number of
unmeasured source events, and sorted `evt_` lineage.
A mixed or incomplete row has overall `unmeasured` coverage while preserving any partial
actual or estimated values.
The run document exposes the root roll-ups in `meter_rollups`; every hierarchy node also
has separate `self_meters` and `total_meters`.

## Event identity and reconciliation

Writers should provide a stable `evt_` ID from the authoritative request boundary.
Metaproc derives a deterministic `evt_` ID for older adapter events that lack one.
Identical duplicates are retained once.
Reusing one ID with different metrics or meter quantities fails the build rather than
silently choosing a value.

An event with no adapter timestamp receives a fixed UTC sentinel; source modification
time remains freshness metadata but is excluded from derived identity.
This keeps regenerated IDs stable after a run is synchronized and rehydrated on another
machine.

## Provider extensions

Plugins register a `ProviderMeterSource` when a parsed provider response contains
structured nested usage.
The hook returns only `ProviderMeterObservation` values: safe
provider/product/model/request identity, common API counters, and typed meter
quantities. The model deliberately has no credential, header, prompt, URL-query, or
request-body field, and rejects unknown fields.

When an LLM session identifies its provider but exposes no authoritative request count,
the built-in extractor emits an `unmeasured` request meter.
It never turns the absence of billing telemetry into a zero or estimates a provider
quota from the number of Metaproc steps.

## Budgets and terminal finalization

An authored process may declare `resource_budgets`. Each `ResourceBudgetSpec` has a
self-identifying `bud_` ID, run/step/provider/model/tool scope, either one canonical
metric or one exact provider-meter key, an explicit canonical unit, a threshold, a
near-threshold ratio, and an `observe`, `warn`, or `refuse-new-work` posture.
The common evaluator reports `within`, `near`, `exceeded`, or `unmeasured`; unavailable
evidence is never rendered as zero.
Existing step `token_budget` and `max_budget_usd` guards are projected into the report
and continue to enforce exactly where they did before.

`run-process` finalizes the trio before releasing its orchestrator lease on success,
failure, timeout, or graceful cancellation.
The original exception always wins over an additive reporting failure.
For an abruptly interrupted inactive run, `metaproc status` can reconstruct missing
reports from the immutable run config and persisted events without making a provider
call. Repeated terminal, resume, status, or rehydration finalization preserves report
bytes when substantive evidence is unchanged.

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing. -->
