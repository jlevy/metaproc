---
type: is
id: is-01m0tc0ysdxt71nnkvhycc8qsq
title: Delete persistent gateway and tmux cloud execution
kind: feature
status: closed
priority: 1
version: 4
labels: []
dependencies: []
parent_id: is-01m0tc0msnehs7xyvhpbekkw45
created_at: 2026-08-24T17:10:24.812Z
updated_at: 2026-08-24T18:02:06.503Z
closed_at: 2026-08-24T18:02:06.503Z
close_reason: Removed the obsolete command, split-state, gateway, and hybrid surfaces; preserved local execution and full-cloud Batch paths. Focused cloud/CLI tests, explicit local/resume regressions, full 4,259-test suite, lint/type/link/public-hygiene/supply-chain/browser/Markdown checks, vulnerability audits, and distribution smoke all pass.
resolution: null
duplicate_of: null
---
Remove gcp remote-run, gcp remote, self-install gateway support, status auto-routing, gateway env vars, tests, and maintained docs. Disposable metaproc gcp run tasks replace SSH/IAP/tmux for arbitrary commands and Filestore inspection. Consumer-owned legacy data migration and deletion of any live unmanaged VM remain downstream operational work, not runtime compatibility.
