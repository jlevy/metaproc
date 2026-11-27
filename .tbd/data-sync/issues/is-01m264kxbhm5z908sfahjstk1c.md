---
type: is
id: is-01m264kxbhm5z908sfahjstk1c
title: "Model review follow-up: verify retained routes and new-model pricing"
kind: task
status: open
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
due_date: 2026-10-31T00:00:00.000Z
created_at: 2026-09-10T17:07:32.336Z
updated_at: 2026-10-10T09:07:09.713Z
---
Verify unresolved model routes and refresh source-supported pricing independently from catalog acceptance. Retain the existing questions around exact kimi-k2.6 and gemini-3.1-pro-preview-customtools availability and run only separately authorized bounded identity/tool probes. Refresh current OpenAI GPT-6, Anthropic Claude 5.5, Google, DeepSeek, Moonshot, and retiring Vertex MaaS rates with route, region, context tier, cache policy, effective date, and primary-source provenance. Correct expired promotions and moving-alias pricing; do not treat allowlisting as live support.

## Notes

2026-10-10 review found pricing.md last_updated 2026-09-14 but most rows remain reviewed in April-May. DeepSeek rows are materially stale: deepseek-v4-flash is now a compatibility alias for deepseek-flash / V4.1 Flash, and current peak/off-peak prices differ from the May values; DeepSeek says V4 Pro remains served under the current schedule. Source: https://api-docs.deepseek.com/quick_start/pricing/ . New OpenAI GPT-6 and Claude 5.5 rows are absent; Sonnet 5.5 cache-read pricing changed 2026-10-07. Google 3.5 Flash-Lite has no row. Existing Kimi exact-ID uncertainty remains unresolved. No paid inference or price-file changes were made.
