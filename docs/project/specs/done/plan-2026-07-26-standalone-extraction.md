---
title: Metaproc Standalone Extraction
description: Completed plan for extracting, validating, and publishing Metaproc as an independent package.
author: Joshua Levy (github.com/jlevy) with LLM assistance
status: Complete
---
# Metaproc Standalone Extraction

**Date:** 2026-07-26 (completed 2026-08-01)

**Status:** Complete

## Goal

Publish Metaproc as an independently installable, testable, and releasable Python
project. Preserve extracted implementation files byte-for-byte unless the standalone
boundary requires a documented rewrite.

## Required Outcome

- The repository builds and tests without a parent workspace.
- Package metadata, documentation, CI, and release automation target
  `github.com/jlevy/metaproc`.
- The project is licensed under AGPL-3.0-or-later.
- Python 3.12, 3.13, and 3.14 are tested.
- Python and JavaScript dependencies are locked and subject to a 14-day release
  cool-off.
- The wheel contains the CLI, data, packaged help, Agent Skill, and Metabrowser plugin
  assets, including the vendored ELK license.
- Public-hygiene checks cover repository files, archives, and reachable Git metadata.
- A downstream repository can pin one exact commit through a Git submodule.

## Migration Map

| Phase | Contract | Completion Evidence |
| --- | --- | --- |
| Extract | Copy every approved implementation file from the sealed source tree before editing. | Exact-copy hashes match the extraction ledger. |
| Separate | Remove consumer-owned commands, fixtures, schemas, configuration, and documentation. | Domain-boundary and public-hygiene tests pass. |
| Scaffold | Merge the pinned `simple-modern-uv` v0.4.0 structure without replacing project code. | Copier answers, AGPL license, locks, and standalone developer commands are committed. |
| Package | Build source and wheel distributions and inspect required and forbidden contents. | Clean-wheel CLI, plugin, data, documentation, and skill smoke tests pass. |
| Verify | Run formatting, lint, type, test, audit, and distribution gates locally and in CI. | `make verify` and the supported Python matrix pass. |
| Integrate | Push an exact standalone commit and pin it from the consumer repository as a submodule. | Both repositories resolve to the same Metaproc commit. |

## Guardrails

- Never reconstruct extracted files from memory.
  Start from the complete source blob, verify its hash, and then make any intentional
  standalone rewrite.
- Do not graft source Git objects or private history into this repository.
- Do not make the consumer submodule an undeclared production dependency.
  Published package versions remain the runtime integration contract.
- Do not publish a release until trusted publishing, package metadata, artifact
  contents, and CI are green for the exact tag target.
- Keep cloud support optional; local planning and execution must work without GCP
  packages or credentials.

## Validation

The handoff gate is `make verify`. It installs both committed locks, checks Python and
browser code, verifies documentation formatting and public hygiene, runs the complete
test suite, audits locked dependencies, builds both distributions, inspects their
contents, and exercises the installed wheel in an isolated environment.

CI repeats lint and distribution checks once and runs tests on Python 3.12, 3.13, and
3.14. The release workflow checks out the exact release tag and repeats the full gate
before publishing through PyPI trusted publishing.

## Completion Status

The sealed source tree has been copied and the framework boundary has been separated
from its former consumer.
Standalone scaffolding, AGPL licensing, dependency locks, public documentation, artifact
validation, CI, and an initial downstream submodule pin are complete.
Repository workflow is agent-facing: `AGENTS.md`, tbd beads, development guidance,
runbooks, and executable process specifications define the process without GitHub issue
or pull request forms.

The initial release-readiness review produced 101 tracked findings.
Every release blocker was fixed or closed with an explicit design decision, and the one
external access blocker was resolved when the repository became public.

The first public release,
[v0.2.0](https://github.com/jlevy/metaproc/releases/tag/v0.2.0), was published on
2026-08-01 through PyPI trusted publishing after the exact tag passed the complete
release gate. No `v0.1.0` tag or PyPI distribution was published.
The downstream integration is pinned to an exact standalone commit, and the public
repository can be cloned by hosted downstream CI without cross-repository credentials.

The deferred timing-sleep cleanup in `mp-wgax` is a non-blocking test-quality ratchet.
Per-test timeouts and the complete suite remain the governing flake backstops.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
