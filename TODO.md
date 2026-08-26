# Metaproc Roadmap

This index stays brief; tbd beads and linked plans hold implementation detail.

## Current Release

- **[v0.3.0](https://github.com/jlevy/metaproc/releases/tag/v0.3.0)** (`mp-wg5f`):
  published from tag `4d518d8`. Pull request 38 merged immediately above the tag at
  `6ac9c65`, so co-development work pins that post-release main commit rather than
  assuming the published wheel contains pull request 38.

## Active Development

- **Native mapped composite scopes** (`mp-0iy8`): consolidate the reviewed runtime
  corrections on released `main`, route mapped local leaves through one run-owned
  RunPool, and pass the exact-head verification gate `mp-nxs9` before downstream smoke
  testing
  ([plan](docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md)).

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
