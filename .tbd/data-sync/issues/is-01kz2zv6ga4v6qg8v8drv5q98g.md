---
type: is
id: is-01kz2zv6ga4v6qg8v8drv5q98g
title: Sanitize Git hook environment before verification
kind: bug
status: closed
priority: 1
version: 3
labels:
  - direct-main
  - safety
dependencies: []
parent_id: is-01kz2x7xfhk0qsxn4ytw7et2bw
created_at: 2026-08-03T04:59:19.429Z
updated_at: 2026-08-03T05:06:09.267Z
closed_at: 2026-08-03T05:06:09.262Z
close_reason: Implemented hook Git-environment isolation in ce03724; full pre-push gate completed without mutating checkout refs.
---
Pre-push verification inherited Git's repository-local environment into tests that create synthetic repositories, allowing fixture git commands to mutate the real checkout refs. Unset repository-local Git variables for the verify command and prove the hook runs safely.
