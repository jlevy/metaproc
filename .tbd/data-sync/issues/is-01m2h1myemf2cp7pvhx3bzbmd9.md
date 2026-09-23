---
type: is
id: is-01m2h1myemf2cp7pvhx3bzbmd9
title: "PR #82 review R4: Preserve compatibility for existing auth-capable adapters"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m2h1m2v9ayz833z46hqah02t
created_at: 2026-09-14T22:47:19.249Z
updated_at: 2026-09-14T23:24:18.430Z
closed_at: 2026-09-14T23:24:18.430Z
close_reason: Addressed in the PR branch with namespace isolation, descriptor-safe all-or-nothing capture, protocol compatibility, nonblocking completion processing, documented collision semantics, complete operator/design docs, focused regressions, GTIA compatibility checks, and a green make verify gate.
resolution: null
duplicate_of: null
---
PR #82 review R4 (High). Adding native_session_log_sets to the runtime-checkable AuthCapableCliAdapter protocol makes older structural adapters fail pool eligibility checks. Split the optional capability or add a missing-method fallback and test the previous public surface.
