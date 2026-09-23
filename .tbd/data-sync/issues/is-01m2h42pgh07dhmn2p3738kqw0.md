---
type: is
id: is-01m2h42pgh07dhmn2p3738kqw0
title: Scope public-hygiene ref scan to current reachable history
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
created_at: 2026-09-14T23:29:47.023Z
updated_at: 2026-09-14T23:31:08.179Z
closed_at: 2026-09-14T23:31:08.178Z
close_reason: Restricted ref-name hygiene scanning to refs whose tips are reachable from HEAD, added an unmerged-sibling regression, and confirmed both the focused 17-test suite and the live public-hygiene gate pass.
resolution: null
duplicate_of: null
---
GitHub Actions checks out every remote ref, so the public-hygiene gate scans unrelated unmerged branch names and can fail every PR and main build. Align the implementation with its reachable-history contract by scanning only refs whose tips are merged into HEAD, and add regression coverage for an unrelated sibling branch.
