# Metaproc Roadmap

This index stays brief; tbd beads and linked plans hold implementation detail.

## Current Release

- **[v0.4.0](https://github.com/jlevy/metaproc/releases/tag/v0.4.0)** (`mp-1n2a`):
  published from tag `2a0aade`, which is the squash merge of pull request 73 and the
  current tip of `main`, so the published wheel and post-release `main` are the same
  tree. The release is a minor, not a patch: the delta since v0.3.0 removes public CLI
  surface, renames the four adapter modules, changes a CLI exit code, and changes the
  default scope failure policy.

## Active Development

- **Process-tree and host safety** (`mp-bd6v`; umbrella feature `mp-qigc`): incubate a
  standalone `safeproc` package with owned pre-execution supervision and brokerless
  monitoring of existing process trees; add cross-platform host admission and
  containment without a standalone pool; integrate Metaproc’s retained RunPool through
  that boundary; and defer any pool-extraction spike until the seam has operating
  evidence
  ([system plan](docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md),
  [package plan](docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md)).

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

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
