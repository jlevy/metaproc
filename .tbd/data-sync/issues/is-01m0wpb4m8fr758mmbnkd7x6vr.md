---
type: is
id: is-01m0wpb4m8fr758mmbnkd7x6vr
title: Persist code-step failure state and diagnostics in run-process
kind: bug
status: closed
priority: 0
version: 5
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - live-smoke
  - observability
dependencies: []
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
created_at: 2026-08-25T14:49:13.095Z
updated_at: 2026-08-25T15:58:11.703Z
closed_at: 2026-08-25T15:58:11.702Z
close_reason: "Metaproc 349f63e passes the deterministic negative path with durable nonempty failed status/diagnostics and no provider work, plus fresh exact-pinned GTIA positive run run-20260825T155153Z-gtia-v30pre-l1-ui-contracts-01, 159 ms zero-work resume, and receipt rcpt_4ma9qvaff0xkt5. Full make verify remains green and PR #47 carries the evidence map."
resolution: null
duplicate_of: null
---
Live GTIA v3.0-pre negative intake on exact Metaproc b5c47217019cd2e59cfc39e905946e02e1be0a05 exposed a fail-closed observability defect. A root mode: code handler raised a deliberate source-attestation mismatch; run-process printed FAILED and exited 1, and the trace has an error step span, but process-events records step_fail with an empty error, metaproc trace shows an empty error.message, and metaproc status reports Status: COMPLETE with intake missing rather than failed. No RunPool or provider work occurred. Reproduce with a deterministic code-handler exception, persist a sanitized actionable failure reason and terminal failed state, make status/trace/tail agree with the CLI, and cover both --only intake and ordinary dependency-blocking execution. Keep the fix generic and narrow; do not add GTIA knowledge or new scheduling machinery.

## Notes

Precommit senior review complete with no unresolved findings. Fixed unknown-condition precedence and carried-failure partial-resume projection during review. Operator reference, core architecture, and definitive PR37 plan are synchronized. Final make verify: 4,393 passed, 8 skipped; Ruff/BasedPyright/links/public hygiene/browser/supply-chain/npm+uv audits/build/installed-wheel smoke green. Commit 349f63e011ed3db0ea5712e4680353657be0fd0d is pushed in stacked PR #47 (https://github.com/jlevy/metaproc/pull/47), based on PR #37 exact b5c47217019cd2e59cfc39e905946e02e1be0a05. GitHub reports no checks configured for this stacked branch; the full pre-push make verify is green. Do not merge or close this bead until exact-pin GTIA negative-attestation and fresh positive L1 live revalidation both pass.
