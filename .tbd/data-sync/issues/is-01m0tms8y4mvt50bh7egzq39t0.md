---
type: is
id: is-01m0tms8y4mvt50bh7egzq39t0
title: Reject changed immutable run inputs on resume
kind: bug
status: closed
priority: 0
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels: []
dependencies: []
parent_id: is-01m0r93je6fk789d26aef6wx11
created_at: 2026-08-24T19:43:30.244Z
updated_at: 2026-08-24T20:07:29.979Z
closed_at: 2026-08-24T20:07:29.963Z
close_reason: "Fixed in Metaproc PR #37 at 5b45520. Resume now fail-closes on changed resolved variables, preserves only the released empty-map omission and known RUNS_DIR Filestore aliases, and rejects malformed/null maps without exposing values. Focused integration: 40 passed. Full local/pre-push: 4,351 passed, 8 skipped. Exact-head GitHub CI runs 32771822723 and 32771867783 both passed all five jobs."
resolution: null
duplicate_of: null
---
PR #37 M0 resume hardening. run-config.yaml persists resolved variables but _validate_run_config currently checks only process name and run directory, so the same RUN_ID can resume against a different request, pipeline/source revision, or process input while retaining old state. Add the smallest generic fail-closed comparison for immutable resolved variables, normalizing only the known RUNS_DIR Filestore mount aliases (and preserving explicitly mutable auth/concurrency as evented launch policy). Cover unchanged resume, changed domain variable rejection, and equivalent mount-alias acceptance in the mapped-scope resume/integration tests. This completes the runnable slice; do not add a consumer metadata framework.

## Notes

Implemented in PR #37 using the existing run-config variables map: resume now rejects added, removed, or changed resolved variables, exposes only field names, and normalizes only known Filestore RUNS_DIR aliases. TDD red phase reproduced the bug; focused run-config/resume integration passes 39 tests; full make verify passes 4,350 tests with 8 skipped plus lint, type, docs, browser, supply-chain, dependency audit, distribution, and installed-wheel checks. Precommit review found one missing operator-facing resume note; fixed it. Awaiting commit, push, and exact-head CI.
