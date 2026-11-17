# Metaproc Roadmap

This index stays brief; tbd beads and linked plans hold implementation detail.

## Current Release

- **[v0.6.1](https://github.com/jlevy/metaproc/releases/tag/v0.6.1)**: the changelog,
  [release notes](docs/project/releases/v0.6.1.md), and publishing pointer match the
  tree. The tag is the owner’s to create, so the tag SHA here is
  `PLACEHOLDER — fill in when the tag exists`, as is the pull request it merges.
  The release is a patch, not a minor: it carries four Metabrowser plugin fixes, every
  one of them wrong since v0.4.0, and lock-only security bumps for `pyasn1`,
  `opentelemetry-api`, and `certifi`, none of which is a declared dependency.
  No public CLI surface, process-file field, runtime artifact shape, plugin entry point,
  or adapter default changed, and no dependency floor moved.
  The browser SDK 0.7 port’s plugin code ships, because it runs correctly on the pinned
  Metabrowser 0.9.1; what was withdrawn before release is its `metabrowser==0.12.0` pin
  and the manifest’s `sdk_version = "0.7"`, because 0.12.0 declares `softschema==0.8.1`
  while Metaproc requires `softschema>=0.9.1,<0.10`, so `metaproc[browser]` could not be
  installed from PyPI at all.
  Its compatibility notes carry forward the unverified GCP image interpreter (`mp-mo74`)
  and the Gemini `respectGitIgnore` default (`mp-x1qh`).

- **[v0.6.0](https://github.com/jlevy/metaproc/releases/tag/v0.6.0)** (`mp-f7g0`):
  published from tag `4289836`, the merge of pull request 120 and the tip of main at the
  time, so there was no gap between the published wheel and post-release main.
  The release is a minor, not a patch: the `gcp run` file console adds CLI surface, the
  `softschema` and `frontmatter-format` floors rise to `0.9.1` and `0.5.1`, and a mapped
  composite now opens as many item scopes as the run ceiling admits where it used to
  stop at 32, so a process tuned against that implicit cap runs more work at once.
  It shipped without pull request 117, which stays open under review.

## Release Follow-Ups

- **Re-port the Metabrowser plugin to browser SDK 0.7**: the port’s plugin code is in
  main and runs on the pinned Metabrowser 0.9.1, so only the host contract was withdrawn
  for v0.6.1 — the `metabrowser==0.12.0` pin, its cool-off exception and cutoff, and the
  manifest’s `sdk_version = "0.7"`. 0.12.0 declares `softschema==0.8.1` while Metaproc
  requires `softschema>=0.9.1,<0.10`, and `[tool.uv] override-dependencies` resolves
  that only for this workspace: an override never reaches wheel metadata, so the
  published extra could not install.
  Blocked on a Metabrowser release whose declared range admits Softschema 0.9. Once one
  exists, restore the pin, the cutoff entry, and the manifest version together, verify
  `metaproc[browser]` installs from a built wheel, and carry it in v0.7.0 rather than a
  patch, since discovery refuses any other SDK and the pair moves as one.
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
  The image build lives outside this repository, so v0.5.0 and v0.6.0 both went out
  unverified; until the image is confirmed or rebuilt on 3.14 and re-pinned, dispatch
  with `--no-wheel` or from 0.4.x, as the release notes and
  `metaproc help cloud-dispatch` say.
  Two consecutive releases have now shipped around this, so confirming the image is
  worth doing before a third.

## Active Development

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
