---
type: is
id: is-01kyx5z0gr2j3v0tvpgmnr18e1
title: Restore public_hygiene CI for historical commit metadata
kind: bug
status: closed
priority: 2
version: 2
labels:
  - ci
  - public-hygiene
dependencies: []
created_at: 2026-07-31T22:50:49.239Z
updated_at: 2026-08-01T04:05:31.382Z
closed_at: 2026-08-01T04:05:31.381Z
close_reason: "Completed on PR #3 branch: tbd 0.4.2/project guidance refreshed; softschema and frontmatter-format 0.4 adopted; all review findings and hygiene/link gates resolved; final make verify passed with 3,793 tests and distribution/audit checks."
---
Metaproc CI public_hygiene fails on historical personal-email and private-PR metadata already present on main and on PR #3 before the SoftSchema fix. Resolve separately without rewriting unrelated PR #3 changes; likely define an explicit safe baseline or sanitize the public-history check. Evidence: https://github.com/jlevy/metaproc/actions/runs/30670902833/job/91288222519
