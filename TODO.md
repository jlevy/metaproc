# Metaproc Roadmap

This index stays brief; tbd beads and linked plans hold implementation detail.

## Current Release

- **[v0.4.0](https://github.com/jlevy/metaproc/releases/tag/v0.4.0)** (`mp-1n2a`):
  published from tag `2a0aade`, the squash merge of pull request 73. The release is a
  minor, not a patch: the delta since v0.3.0 removes public CLI surface, renames the
  four adapter modules, changes a CLI exit code, and changes the default scope failure
  policy.

## Release Follow-Ups

- **Decide the Gemini `respectGitIgnore` default** (`mp-x1qh`): v0.4.0 shipped
  `respectGitIgnore: False`, so a Gemini step can read ignored files including `.env`.
  Disclosure has landed — the amended [v0.4.0 notes](docs/project/releases/v0.4.0.md)
  carry it, the shipped design doc states it, and the
  [v0.4.1 notes](docs/project/releases/v0.4.1.md) repeat it as a standing caution — so
  what remains is the behavior question disclosure does not settle: whether reading
  ignored files should become opt-in per step or per profile.

## Active Development

- **v0.4.1 release** (`mp-m22t`): the changelog,
  [release notes](docs/project/releases/v0.4.1.md), and publishing pointer match the
  tree. What remains is the tag itself and the publish it triggers; § Current Release
  moves to v0.4.1 once that tag exists.
  The release is a patch, not a minor: nothing public was removed or renamed and no
  adapter default changed.
  The one behavior change is that an unrecognized explicit model name now fails the step
  instead of silently running the adapter default, which is the fix itself and is
  recorded as a compatibility note.
- **Process-tree and host safety** (`mp-bd6v`; umbrella feature `mp-qigc`): incubate a
  standalone `safeproc` package with owned pre-execution supervision and brokerless
  monitoring of existing process trees; add cross-platform host admission and
  containment without a standalone pool; integrate Metaproc’s retained RunPool through
  that boundary; and defer any pool-extraction spike until the seam has operating
  evidence
  ([system plan](docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md),
  [package plan](docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md)).
- **Execution stability, operator diagnostics, and flexibility** (`mp-7p3z`): the
  [active plan](docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md)
  puts prominent, actionable operator diagnostics first (`mp-5les`) and maps the
  review’s seven open findings to implementation owners and acceptance evidence.
  It also owns the mapped-scope follow-ons under `mp-82ls`, `mp-tibt`, and `mp-rrfn`;
  their existing pauses and evidence requirements remain in effect.
  Contract-failure extensions roll up through `mp-d019`: run-wide abort, plugin
  classifiers, and per-kind counts, with
  [design rationale](docs/project/design/contract-failure-primitives.md).
- **Model catalog compatibility** (`mp-qmr0`): the
  [follow-up plan](docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md)
  owns client upgrades, October Vertex retirements, and uncertain routes and pricing.
  [Catalog maintenance](docs/project/model-catalog-maintenance.md) defines the recurring
  review procedure.

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
