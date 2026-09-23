---
type: is
id: is-01m269mehrtqg1chz8dqsfzhwr
title: Complete contract-failure semantics
kind: epic
status: open
priority: 2
version: 6
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
child_order_hints:
  - is-01m23c18ryx66nfjnezj79g4c9
  - is-01m23c1942j3ngpqkzdhkpwyyx
  - is-01m23c19ehv210xqw508hktqt2
created_at: 2026-09-10T18:35:12.822Z
updated_at: 2026-09-10T18:49:55.015Z
---
Own the three remaining contract-failure extensions under the consolidated execution plan: run-wide fail_run propagation (mp-cl0d), plugin classifier registration (mp-3uaf), and per-kind failure counts (mp-m4vi). Preserve the implemented foundation and declarative retry behavior. Design rationale now lives in docs/project/design/contract-failure-primitives.md; PR75 F10 acceptance remains mp-rrkw.
