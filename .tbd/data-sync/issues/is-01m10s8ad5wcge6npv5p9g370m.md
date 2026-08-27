---
type: is
id: is-01m10s8ad5wcge6npv5p9g370m
title: Bootstrap consumer plugins before run visualization reconstruction
kind: bug
status: closed
priority: 0
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - runtime-projection
dependencies: []
parent_id: is-01m0rm18kbm24khxjemevb1ybv
created_at: 2026-08-27T04:57:04.164Z
updated_at: 2026-08-27T05:10:18.263Z
closed_at: 2026-08-27T05:10:18.255Z
close_reason: Fixed generically in e0882c2; 58 focused tests, 4,494-test full verification, completed-run consumer projection with no warnings or unaccepted outputs, and all five GitHub checks pass.
resolution: null
duplicate_of: null
---
A completed mapped-composite consumer run exposed that the Metabrowser sidekick runs under the metab CLI rather than the Metaproc CLI, so installed metaproc.plugins were never loaded before plan reconstruction rediscovered runtime-produced fan-out sources. Valid domain softschema envelopes then appeared unknown and the viz-model hook returned HTTP 400. Bootstrap generic consumer plugins at the visualization boundary and add a consumer-neutral regression proving typed fan-out discovery. Preserve schema validation; do not add an untyped parser, schema registry, or domain special case.

## Notes

Fixed generically in the Metabrowser viz-model sidekick by loading installed Metaproc consumer plugins before typed plan reconstruction. Added a consumer-neutral runtime fan-out regression. Focused projection/browser suite: 58 passed. Real downstream run proof: 25 completed tasks, 25 succeeded terminal attempts, 57 accepted outputs, zero unaccepted outputs, no validation warnings. Full make verify: 4,494 passed, eight tracked credential/infrastructure skips; lint, types, public hygiene, dependency audits, distributions, and installed-wheel smoke passed.
