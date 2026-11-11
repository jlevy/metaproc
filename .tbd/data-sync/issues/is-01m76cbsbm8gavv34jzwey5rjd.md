---
type: is
id: is-01m76cbsbm8gavv34jzwey5rjd
title: Run the advisory audit on a schedule and enable Dependabot alerts
kind: task
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m76a31xm6ap52ytabzbmhdt1
created_at: 2026-11-11T22:41:23.827Z
updated_at: 2026-11-11T22:41:23.827Z
---
Deferred from PR #122 (https://github.com/jlevy/metaproc/pull/122), suggestion B2 of review B (https://github.com/jlevy/metaproc/pull/122#pullrequestreview-5866218020).

.github/workflows/ci.yml:3-11 runs CI only on pushes to main and on pull requests. The four pyasn1 advisories entered the GitHub Advisory Database on 2026-10-30; the last run on main was 2026-10-26, and the first run to see them was PR #121's lint job on 2026-11-11, 11.8 days later. Dependabot alerts are disabled for the repository and there is no .github/dependabot.yml.

Work: add a daily scheduled workflow on main that runs make audit with contents: read and the existing pinned actions, and enable Dependabot alerts (alerts only, no version updates, so the 14-day cool-off is untouched). Enabling alerts is a repository setting the owner changes.
