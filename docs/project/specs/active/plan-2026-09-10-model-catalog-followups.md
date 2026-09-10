---
title: Model Catalog Compatibility Follow-Ups
description: Complete the client, retirement, route, and pricing decisions left open by the September model catalog review.
author: Codex with maintainer review
date: 2026-09-10
status: In Review
tracking_bead: mp-qmr0
---
# Plan: Model Catalog Compatibility Follow-Ups

**Date:** 2026-09-10 (last updated 2026-09-10)

**Status:** Catalog consolidation is merged; three compatibility follow-ups remain open.

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

- [ ] **Client compatibility — `mp-xgrd`:** coordinate pinned clients and deployment
  images for Fable 5.1. The September review found Claude Code requires at least 2.1.257
  while the repository pins 2.1.234, and Pi 0.84.2 lacks the native ID. Recheck those
  facts before selecting upgrades, apply the supply-chain policy, and verify exact model
  identity and representative tool calls through each intended route.
  Preserve version checks and add Pi acceptance only when its deployed catalog supports
  the ID.
- [ ] **Retirement migration — `mp-4w8q`:** review replacement routes by October 1 for
  the announced October 21 retirement of GLM 5, GLM 4.7, DeepSeek V3.2, and Kimi K2
  Thinking on Vertex MaaS. Recheck the provider’s schedule, identify affected accepted
  IDs and configurations, test replacements, and record migration and historical-ID
  retention decisions before removing acceptance.
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
