---
type: is
id: is-01m7p8qg31xk7q5jxat8g8vdm1
title: Price Claude 1-hour cache writes at the 1h rate
kind: bug
status: open
priority: 3
version: 2
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m7p3pwxkys2pdd65g2kagg7b
created_at: 2026-11-18T02:45:44.161Z
updated_at: 2026-11-18T02:57:26.823Z
---
Claude Code 2.1.325 writes cache entries with a 1-hour TTL (usage.cache_creation.ephemeral_1h_input_tokens), but metaproc prices all cache_creation tokens at the 5-minute cache_write_per_1m rate, understating cache-write cost ~1.6x (Opus 5.5 probe: computed $0.040 vs CLI total_cost_usd $0.059). Parse the ephemeral_5m/ephemeral_1h split and price 1h writes at the 1h rate (2x base input per Anthropic pricing), falling back to the current behavior when the split is absent.

## Notes

Scope narrowed 2026-11-18: Claude attempts normally carry the CLI's total_cost_usd, which estimate_list_cost and the trace extractor prefer, and the probe showed it matches published 1h-write prices. Only the pricing-table fallback (no reported cost) understates 1h writes. Deferred from the November refresh to avoid a cost-model change in a harness-upgrade release.
