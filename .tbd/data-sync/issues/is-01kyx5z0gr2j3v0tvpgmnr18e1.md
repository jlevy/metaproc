---
type: is
id: is-01kyx5z0gr2j3v0tvpgmnr18e1
title: Restore public_hygiene CI for historical commit metadata
kind: bug
status: open
priority: 2
version: 1
labels:
  - ci
  - public-hygiene
dependencies: []
created_at: 2026-07-31T22:50:49.239Z
updated_at: 2026-07-31T22:50:49.239Z
---
Metaproc CI public_hygiene fails on historical personal-email and private-PR metadata already present on main and on PR #3 before the SoftSchema fix. Resolve separately without rewriting unrelated PR #3 changes; likely define an explicit safe baseline or sanitize the public-history check. Evidence: https://github.com/jlevy/metaproc/actions/runs/30670902833/job/91288222519
