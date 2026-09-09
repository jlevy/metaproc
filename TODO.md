# Metaproc Roadmap

This index stays brief; tbd beads and linked plans hold implementation detail.

## Current Release

- **[v0.4.0](https://github.com/jlevy/metaproc/releases/tag/v0.4.0)** (`mp-1n2a`):
  published from tag `2a0aade`, which is the squash merge of pull request 73 and the
  current tip of `main`, so the published wheel and post-release `main` are the same
  tree. The release is a minor, not a patch: the delta since v0.3.0 removes public CLI
  surface, renames the four adapter modules, changes a CLI exit code, and changes the
  default scope failure policy.

## Release Follow-Ups

- **Amend the v0.4.0 release notes** (`mp-lto0`): a post-publication review of all 26
  merged pull requests found roughly 23 undocumented user-visible changes and 7
  misdescriptions. PyPI is immutable, so the fix is amending
  [the notes](docs/project/releases/v0.4.0.md) and the GitHub release body.
- **Disclose the Gemini `respectGitIgnore` posture change** (`mp-x1qh`): v0.4.0 shipped
  `respectGitIgnore: False`, so a Gemini step can read ignored files including `.env`.
  The shipped design doc says so; the release notes never did.
  Decide whether disclosure suffices or the default should become opt-in.

## Active Development

- **Process-tree and host safety** (`mp-bd6v`; umbrella feature `mp-qigc`): incubate a
  standalone `safeproc` package with owned pre-execution supervision and brokerless
  monitoring of existing process trees; add cross-platform host admission and
  containment without a standalone pool; integrate Metaproc’s retained RunPool through
  that boundary; and defer any pool-extraction spike until the seam has operating
  evidence
  ([system plan](docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md),
  [package plan](docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md)).
- **Mapped-scope follow-on** (`mp-tibt`, `mp-82ls`, `mp-0iy8`): 21 open beads carried by
  the shipped
  [mapped-scope runtime](docs/project/specs/done/plan-2026-08-25-consolidated-mapped-scope-runtime.md)
  and not addressed in v0.4.0 — durable task records and replay parity, the retry-later
  cluster, mapped-scope parity and scale verification, and two decomposition designs.
  Their parent beads are closed, so this work currently rolls up to nothing.
- **Contract failure primitives** (`mp-cl0d`, `mp-3uaf`, `mp-m4vi`): phase 2 remnants of
  the [spec](docs/project/specs/active/plan-2026-08-20-contract-failure-primitives.md),
  which tracked them only as checkboxes.
  `fail_run` still does not stop a run.

## Deferred Quality Ratchets

- **Post-release hardening** (`mp-7kwn`): add PyPI attestations after the reviewed
  action clears the supply-chain cool-off (`mp-s901`), add the checked-JavaScript
  promise-safety overlay when its dependency graph is safe (`mp-608l`), and complete the
  incremental `noImplicitAny` migration (`mp-kptm`).
- **Timing-test cleanup** (`mp-wgax`): replace bounded sleeps only when observed
  flakiness justifies the change; the complete suite and per-test timeouts remain the
  backstops.

## Completed Workstreams

- [Standalone extraction](docs/project/specs/done/plan-2026-07-26-standalone-extraction.md):
  independent public repository, AGPL package, CI, trusted publishing, v0.2.0, and exact
  downstream pin.
- [Focused resource observability](docs/project/specs/done/plan-2026-08-03-focused-resource-observability.md):
  ledger-backed metrics, compatibility reads, budgets, terminal reporting, recovery, and
  browser and CLI projections.
- [Consolidated mapped-scope runtime](docs/project/specs/done/plan-2026-08-25-consolidated-mapped-scope-runtime.md)
  (`mp-0iy8`): `for_each` on a composite step, one recursive execution context, one
  run-owned RunPool for local mapped leaves, and mapped scopes projected through the
  existing plan, status, trace, and pool views.
  Shipped in v0.4.0; its follow-on work is listed under Active Development.
- [Documentation organization](docs/project/specs/done/plan-2026-08-26-documentation-organization.md):
  the shipped documentation set moved into `src/metaproc/docs/`, and `metaproc help`
  grew from 3 topics to 17.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
