---
type: is
id: is-01m0rrzrh3cwy5jzb9mdyf0zk5
title: "PR32 F4: propagate credential-pool policy to scalar agent leaves"
kind: task
status: closed
priority: 1
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
  - pr-review
dependencies: []
parent_id: is-01m0rm18400gvqf9d61s4138mg
created_at: 2026-08-24T02:18:28.259Z
updated_at: 2026-08-24T02:40:19.762Z
closed_at: 2026-08-24T02:40:19.762Z
close_reason: "Addressed in Metaproc PR #32 commits 7e8034d and 243d896. The plan now incorporates every F1-F8 correction, the disposition is posted at https://github.com/jlevy/metaproc/pull/32#issuecomment-5390055801, and canonical CI is green. Runtime implementation remains tracked by mp-p0sn, mp-zssw, mp-0ukj, mp-0cyw, mp-1af0, and mp-rrfn."
resolution: null
duplicate_of: null
---
Require RunExecutionContext to carry auth and pool dispatch policy into scalar agent leaves and add tests that assert mapped child invocations use the intended pool labels. Review: https://github.com/jlevy/metaproc/pull/32#issuecomment-5389812461
