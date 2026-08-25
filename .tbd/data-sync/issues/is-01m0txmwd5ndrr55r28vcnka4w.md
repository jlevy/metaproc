---
type: is
id: is-01m0txmwd5ndrr55r28vcnka4w
title: Write the 0.3.0 release notes and convert the CHANGELOG section
kind: task
status: closed
priority: 1
version: 3
labels:
  - release,docs
dependencies: []
parent_id: is-01m0tx34t3n8g39jjbhzdrrpwf
created_at: 2026-08-24T22:18:23.525Z
updated_at: 2026-08-25T02:55:25.968Z
closed_at: 2026-08-25T02:55:25.967Z
close_reason: "Written and shipped. CHANGELOG [Unreleased] converted to [0.3.0] - 2026-08-25 with links updated, docs/releases/v0.3.0.md written per release-notes-guidelines, docs/publishing.md repointed at the current notes. Merged in PR #40."
resolution: null
duplicate_of: null
---
Release checklist step 5. Depends on mp-bn76 filling the CHANGELOG gaps first.

## Work

1. Convert `[Unreleased]` in CHANGELOG.md to `[0.3.0] - <date>` once complete.
2. Update the link refs at the bottom: add a `[0.3.0]` tag link and repoint `[unreleased]` to `compare/v0.3.0...HEAD`.
3. Write docs/releases/v0.3.0.md following `tbd guidelines release-notes-guidelines`, matching the structure of the existing v0.2.0.md and v0.2.1.md.
4. End with a concrete compare link (`v0.2.1...v0.3.0`).
5. Format with `make format-markdown` so Flowmark is satisfied, and keep the standard doc footer.

## Content to cover

The aggregate user-visible delta across 12 merged PRs: durable per-attempt history and crash-safe reconciliation, the resume/retry correctness fixes, actionable invalid-output retry feedback, schema conform, contract failure primitives, scalar launch admission and RunPool-as-a-library, the Gemini CLI minimum-version refusal, and the GCP Batch dispatch hardening.

## Compatibility notes required

- **Code-step outputs are no longer YAML-repaired.** Already drafted in the Changed section. A process whose code handler relied on the repair pass will start reporting `invalid_outputs`.
- **Gemini CLI minimum version.** Confirm whether a previously working setup can now be refused at startup; if so it needs its own note.
- **Replay parity limitation** on the pool-admission path, per the decision recorded in mp-6l5m if option 2 is chosen.
- **Legacy run-tree compatibility.** The CHANGELOG asserts replay "retains status-based compatibility for historical run trees", but mp-g315 (open, P1, blocked) exists precisely to name and test that boundary. Either soften the claim to what is actually covered by tests, or land mp-g315 first. Present coverage is 20 attempt-history assertions in tests/test_io.py, 4 in test_replay_equivalence.py, and two misaddressed-legacy-status refusal tests; that is not the same as proving the mixed-authority fallback.
