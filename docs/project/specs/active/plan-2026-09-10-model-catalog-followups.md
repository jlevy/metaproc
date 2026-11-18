---
title: Model Catalog Compatibility Follow-Ups
description: Complete the client, retirement, route, and pricing decisions left open by the September model catalog review.
author: Codex with maintainer review
date: 2026-09-10
status: In Review
tracking_bead: mp-qmr0
---
# Plan: Model Catalog Compatibility Follow-Ups

**Date:** 2026-09-10 (last updated 2026-11-18)

**Status:** Catalog consolidation is merged. The November refresh (`mp-5212`) resolves
the client and retirement follow-ups; route and pricing verification remains open.

## Overview

Epic `mp-qmr0` owns the remaining decisions from the
[model review](https://github.com/jlevy/metaproc/pull/69#issuecomment-5623133963). Pull
requests [69](https://github.com/jlevy/metaproc/pull/69) and
[76](https://github.com/jlevy/metaproc/pull/76) merged the catalog, explicit-selection
correction, maintenance procedure, and documentation-date fix.
They did not establish live compatibility for every route or refresh model prices.

[Model Catalog Maintenance](../../model-catalog-maintenance.md) owns the recurring
procedure and evidence boundaries.
This plan owns the three outstanding implementation items.
Review the catalog every 30 days and on the procedure’s release and deprecation
triggers; the configured Codex follow-up performs a read-only review.

## Implementation and Acceptance

- [x] **Client compatibility — `mp-xgrd`:** resolved by the November refresh. Claude
  Code 2.1.325 and Pi 1.5.2 were adopted under reviewed cool-off exemptions for Fable
  5.2; every current Claude ID completed a live tool-call probe on the Anthropic
  first-party route
  ([refresh record](../../research/research-2026-11-18-model-and-harness-refresh.md)).
- [x] **Retirement migration — `mp-4w8q`:** resolved by the November refresh. The six
  Vertex MaaS IDs retired on 2026-10-21 were removed and replaced by
  `zai-org/glm-5.2-maas` and `deepseek-ai/deepseek-v4.1-flash-maas`; the `pi-glm5`
  profile became `pi-glm52`. The Vertex MaaS route itself is unprobed.
- [ ] **Uncertain routes and pricing — `mp-jnws`:** establish the exact retained Kimi
  K2.6 ID and Vertex custom-tools availability from primary evidence and bounded account
  probes. Verify model identity and tool calls for new Pi Responses and native Vertex
  routes. Source prices independently, recording currency, units, route, and review date.
  Preserve uncertainty labels until evidence supports changing them.

## Validation and Closure

Keep model existence, CLI parsing, account availability, live compatibility, and price
evidence separate. No default change follows automatically from a catalog refresh.
Obtain the required credentials and budget before paid probes; record the tested route
and date without generalizing across accounts or regions.

Each implementation PR updates the catalog and relevant documentation, includes command
construction and rejection regressions, and passes `make verify` plus CI. A follow-up
closes with its source and validation evidence or an explicit maintainer disposition.
The epic remains open while any of these three decisions is unresolved.

## References

- [Model catalog](../../../../src/metaproc/config/model_catalog.py)
- [Pricing maintenance](../../../../src/metaproc/data/pricing.md)
- [Supply-chain policy](../../../../SUPPLY-CHAIN-SECURITY.md)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
