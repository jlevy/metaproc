---
type: is
id: is-01m76cbqcvcs4sxjvg7afxvd0p
title: "PR #122 A1: no supply-chain record entry for the pyasn1 security bump"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76cbg8653gb7qwb22zsqfgn
hold: null
hold_until: null
created_at: 2026-11-11T22:41:21.817Z
updated_at: 2026-11-11T22:43:37.297Z
started_at: 2026-11-11T22:41:30.232Z
closed_at: 2026-11-11T22:43:37.280Z
close_reason: "Fixed in f16a100: added the pyasn1 0.6.5 entry to Audited Advisory Waivers in SUPPLY-CHAIN-SECURITY.md; flowmark --check, check_links, check_doc_dates, public_hygiene, check_supply_chain and codespell pass."
resolution: null
duplicate_of: null
---
PR #122 review A, finding A1 (Medium): https://github.com/jlevy/metaproc/pull/122#pullrequestreview-5865991209

SUPPLY-CHAIN-SECURITY.md:160-233 (Audited Advisory Waivers) has no entry for the pyasn1 0.6.4 -> 0.6.5 security bump, though every earlier security adoption (urllib3 2.8.0, certifi 2026.10.19, opentelemetry-api 1.45.1) added one in the same change as its lock edit. The PR changes uv.lock:1218-1222 only.

Fix: add a paragraph after the opentelemetry-api entry, before 'Re-review a waiver', naming the four advisories, severity, reachability, cool-off and metadata.
