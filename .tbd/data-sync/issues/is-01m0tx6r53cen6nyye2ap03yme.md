---
type: is
id: is-01m0tx6r53cen6nyye2ap03yme
title: CHANGELOG Unreleased omits five merged PRs of user-visible change
kind: bug
status: closed
priority: 1
version: 3
labels:
  - release,docs
dependencies:
  - type: blocks
    target: is-01m0txmwd5ndrr55r28vcnka4w
parent_id: is-01m0tx34t3n8g39jjbhzdrrpwf
created_at: 2026-08-24T22:10:40.418Z
updated_at: 2026-08-25T02:38:01.587Z
closed_at: 2026-08-25T02:38:01.587Z
close_reason: |-
  Fixed while writing the 0.3.0 notes. All five omitted PRs are now covered (#21 scalar admission and RunPool as a library, #22 Gemini minimum-version refusal, #23 contract failure primitives, #26 schema conform, #30 GCP Batch dispatch hardening), plus #20's chains/fan-in/declared retry.

  Applying release-notes-guidelines also reclassified several existing entries: the durable-attempt-history and item-aligned-chain 'fixes' were defects on development branches for features shipping first in 0.3.0, so they fold into the feature descriptions rather than standing as fixes. Verified against v0.2.1 that aligned chains and fan_in did not exist there, while mode: code and gcp run did — so the code-repair and GCP entries are genuine deltas for 0.2.1 users.
resolution: null
duplicate_of: null
---
## Problem

The `[Unreleased]` section of CHANGELOG.md documents only part of what has landed since v0.2.1. Twelve PRs merged (#17, #20-#31, 97 files, +15,611/-641), but the section covers roughly half of them: durable attempt history, crash-safe reconciliation, the resume/retry fixes, and the YAML-repair scoping change.

Release checklist step 5 requires the notes to describe the aggregate user-visible delta. They currently do not.

## Verified gaps

Keyword scan of the `[Unreleased]` section:

| Topic | Mentions |
|---|---|
| gemini | 0 |
| admission | 0 |
| RunPool | 0 |
| watermark / RFC3339 / symlink / Batch / workspace | 0 each |

The two apparent hits are false positives: "contract" appears only inside the retry-feedback field list (line 42), and "conform" only inside the YAML-repair scoping entry (line 53). Neither describes the underlying change.

## Missing entries

- **PR #21** `feat/scalar-launch-admission` - "Phase B: close the admission hole and support RunPool as a library". New public capability (RunPool usable as a library) plus an admission-correctness fix. 228-line test suite added.
- **PR #22** `fix/gemini-cli-minimum-version` - "Gemini: refuse a CLI below the minimum, instead of failing cryptically mid-run". User-visible behavior change: a previously-accepted Gemini CLI version is now refused at startup.
- **PR #23** `feat/contract-failure-handling` - "Contract failure primitives: keep what validation knows".
- **PR #26** `feat/schema-conform` - "Quote agent-written YAML scalars against their contract's schema". 407-line test suite added; directly affects how agent output is normalized.
- **PR #30** `codex/gcp-run-rfc3339-watermark` - "harden arbitrary Batch command dispatch". Carries RFC3339 log-watermark serialization, safe workspace symlink materialization, baked-dependency preservation on wheel override, generic run logging client reuse, and shipped workspace package installation. The entire GCP batch is undocumented.

PR #20 (`feat/semantic-kernel-rfc`) added the execution-model design doc, architecture doc, and executable reference model. Worth a line if the reference model is consumer-visible; docs-only if not.

## Action

Fill these in, then convert `[Unreleased]` to a `[0.3.0]` section with the release date and update the compare links at the bottom of the file. Mirror the result into docs/releases/v0.3.0.md per the checklist.

## Note on PR #22

Confirm whether refusing a below-minimum Gemini CLI warrants a compatibility note. It can break a working setup on upgrade, which is the kind of thing the 0.2.1 notes called out explicitly.
