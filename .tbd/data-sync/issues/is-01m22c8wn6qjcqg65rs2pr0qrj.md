---
type: is
id: is-01m22c8wn6qjcqg65rs2pr0qrj
title: Reconcile v0.4.0 release records and cut the tag
kind: task
status: closed
priority: 1
version: 3
labels:
  - release
  - release-blocker
dependencies: []
parent_id: is-01m1dbcer80nak10tnbg1jyq52
created_at: 2026-09-09T06:04:22.053Z
updated_at: 2026-09-09T07:33:48.241Z
closed_at: 2026-09-09T07:33:48.240Z
close_reason: "v0.4.0 released. Records reconciled in PR #73 (squash 2a0aade): ten missing CHANGELOG entries, the metabrowser 0.9.0 -> 0.9.1 correction in CHANGELOG and SUPPLY-CHAIN-SECURITY.md, new docs/project/releases/v0.4.0.md, and the publishing.md pointer."
resolution: null
duplicate_of: null
---
The v0.4.0 epic's five original children closed at main 2595662, but eleven pull requests merged after that commit and the release records did not follow.

Found by a release-readiness review at main df01cb7:

- CHANGELOG `[Unreleased]` omitted every user-visible change merged after 2026-09-01: the Gemini model-substitution fix (#67 and the dynamic-resolution routing in #68), Gemini 3.7/3.8 Flash and the `gemini-flash-38` profile (#68), the aggregated launch-error report (#68), the refused-launch exit code (#68), Gemini lane sizing from measured memory (#68), the lazy Metabrowser renderer load (#64), the scope failure policy (#70), and the fail-fast message (#72).
- CHANGELOG stated `metabrowser==0.9.0` while pyproject pins `0.9.1` (#65).
- SUPPLY-CHAIN-SECURITY.md named `metabrowser==0.9.0` in its first-party exception, and described kpress as pinned by 0.9.0 — drift by that file's own stated rule, since #65 advanced pyproject, uv.toml, and uv.lock without the rationale.
- No `docs/project/releases/v0.4.0.md`, no `[0.4.0]` changelog section or link reference, and `docs/publishing.md` still pointed at v0.3.0 as the most recent notes.

Gate at df01cb7: `make lint-check`, `make test` (4,608 passed, 8 skipped), and `make build` (distribution + installed-wheel smoke) all pass locally. `uv audit` cannot run in this container because the network policy blocks api.osv.dev; hosted CI run 34316626425 is green at that exact commit and covers it, along with npm audit and the 3.12/3.13/3.14 matrix.

Done when the records match the tree, `v0.4.0` is tagged from main, the publish workflow succeeds, the released wheel passes the isolated smoke tests in docs/publishing.md, and TODO.md § Current Release names v0.4.0.
