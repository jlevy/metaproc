# Metaproc Roadmap

This index stays brief; tbd beads and linked plans hold implementation detail.

## Current Release

- **[v0.3.0](https://github.com/jlevy/metaproc/releases/tag/v0.3.0)** (`mp-wg5f`):
  published from tag `4d518d8`. Pull request 38 merged immediately above the tag at
  `6ac9c65`, so co-development work pins that post-release main commit rather than
  assuming the published wheel contains pull request 38.

## Active Development

- **v0.4.0 release** (`mp-1n2a`): close the remaining loose ends between `main` and a
  tag — two correctness defects in silent paths, release records that disagree with the
  tree, first-party dependency currency, and stale tracking.
  The release is a minor, not a patch: the delta since v0.3.0 removes public CLI surface
  and changes output and transport contracts.

## Deferred Follow-Ups

- **Metabrowser plugin SDK migration** (`mp-g7l4`): `metabrowser` is held at `0.1.0`
  against a current `0.9.0` because the plugin targets browser SDK 0.1 and 0.9.x
  provides 0.5. It is the optional `browser` extra, so it gates no core command.

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
