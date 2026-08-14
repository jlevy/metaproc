---
type: is
id: is-01kz2x8e020tvdgn7htfszeenx
title: "PR #8 review MP8-09: centralize typed-ID grammar and update docs"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:14:07.361Z
updated_at: 2026-08-03T04:27:18.303Z
closed_at: 2026-08-03T04:27:18.303Z
close_reason: "Fixed in PR #8 working tree: exact hashed GCP run selectors with legacy fallback; exact legacy derived-ID replay; validated collision-bounded width controls; centralized/anchored typed partition matching and dash-writer docs. Focused tests 215 passed, full suite passed except the known checkout-basename test, and Python lint/type checks are clean."
---
docs/resource-rollup.md:41, docs/arch/arch-metaproc-core.md:2125, src/metaproc/commands/run_process.py:597, and src/metaproc/engine/dep_state.py:26. Update normative writer forms to dash IDs, label underscore forms historical, centralize partition matching, and anchor resume identity to the configured run/cohort partition.
