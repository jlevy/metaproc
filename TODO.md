# Metaproc Roadmap

This index stays brief; tbd beads and linked plans hold implementation detail.

## Current Release

- **[v0.5.0](https://github.com/jlevy/metaproc/releases/tag/v0.5.0)** (`mp-avdc`):
  published from tag `0714762`, the merge of pull request 110 and the current tip of
  main, so there is no gap between the published wheel and post-release main.
  The release is a minor, not a patch, because it raises the Python floor to 3.14. Its
  compatibility notes lead with that floor, the unverified GCP image interpreter
  (`mp-mo74`), and Metabrowser not yet reading `.zst`; they also record that a local run
  whose mapped agent steps pin profiles with different pool resources now fails where
  v0.4.1 ran it (`mp-8oak`).

## Release Follow-Ups

- **Decide the Gemini `respectGitIgnore` default** (`mp-x1qh`): v0.4.0 shipped
  `respectGitIgnore: False`, so a Gemini step can read ignored files including `.env`.
  Disclosure has landed — the amended [v0.4.0 notes](docs/project/releases/v0.4.0.md)
  carry it, the shipped design doc states it, and the
  [v0.4.1](docs/project/releases/v0.4.1.md), [v0.5.0](docs/project/releases/v0.5.0.md),
  and [v0.6.0](docs/project/releases/v0.6.0.md) notes repeat it as a standing caution —
  so what remains is the behavior question disclosure does not settle: whether reading
  ignored files should become opt-in per step or per profile.
- **Verify the GCP image interpreter** (`mp-mo74`): Metaproc has required Python 3.14
  since v0.5.0, and container bootstrap refuses the shipped wheel on an image whose
  `/opt/venv` is older.
  The image build lives outside this repository, so v0.5.0 went out unverified and
  v0.6.0 is staged to go out the same way; until the image is confirmed or rebuilt on
  3.14 and re-pinned, dispatch with `--no-wheel` or from 0.4.x, as the release notes and
  `metaproc help cloud-dispatch` say.

## Active Development

- **v0.6.0 release** (`mp-f7g0`): the changelog,
  [release notes](docs/project/releases/v0.6.0.md), and publishing pointer match the
  tree. What remains is the tag itself and the publish it triggers; § Current Release
  moves to v0.6.0 once that tag exists.
  The release is a minor, not a patch: the `gcp run` file console adds CLI surface, the
  `softschema` and `frontmatter-format` floors rise, and a mapped composite now opens as
  many item scopes as the run ceiling admits where it used to stop at 32, so a process
  tuned against that implicit cap runs more work at once.
  Its compatibility notes also carry forward the unverified GCP image interpreter
  (`mp-mo74`) and the Gemini `respectGitIgnore` default (`mp-x1qh`).

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
