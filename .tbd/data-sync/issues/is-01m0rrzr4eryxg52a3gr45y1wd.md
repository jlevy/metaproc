---
type: is
id: is-01m0rrzr4eryxg52a3gr45y1wd
title: "PR32 F3: unify host memory authority and unblock the event loop"
kind: task
status: closed
priority: 0
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
  - pr-review
dependencies: []
parent_id: is-01m0rm18400gvqf9d61s4138mg
created_at: 2026-08-24T02:18:27.854Z
updated_at: 2026-08-24T02:40:19.754Z
closed_at: 2026-08-24T02:40:19.754Z
close_reason: "Addressed in Metaproc PR #32 commits 7e8034d and 243d896. The plan now incorporates every F1-F8 correction, the disposition is posted at https://github.com/jlevy/metaproc/pull/32#issuecomment-5390055801, and canonical CI is green. Runtime implementation remains tracked by mp-p0sn, mp-zssw, mp-0ukj, mp-0cyw, mp-1af0, and mp-rrfn."
resolution: null
duplicate_of: null
---
Make one shared byte authority span RunPool and scalar launches, protect byte decisions with a mutex and claims ledger, make ramp and warm restore re-consult fresh capacity, and move blocking command execution off the shared event loop with an explicit executor ceiling. Review: https://github.com/jlevy/metaproc/pull/32#issuecomment-5389812461
