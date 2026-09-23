---
type: is
id: is-01m0typyf8h0knc7bpetrqe4tx
title: "PR #38 review 8: name removed gateway environment variables"
kind: bug
status: closed
priority: 3
version: 3
labels: []
dependencies: []
parent_id: is-01m0typ5swnqc9v7gee2ymkjs9
created_at: 2026-08-24T22:36:59.752Z
updated_at: 2026-08-24T22:56:48.553Z
closed_at: 2026-08-24T22:56:48.553Z
close_reason: Fixed in 809fccc; exact-head CI run 32786763844 passed all five jobs and disposition published at issuecomment-5402607487.
resolution: null
duplicate_of: null
---
Review finding 8 at issuecomment-5402359572. CHANGELOG.md should explicitly name the two removed METAPROC_* gateway/remote environment variables so stale exports are searchable even though unknown variables are inert.
