# Metaproc Roadmap

This roadmap tracks the remaining work after the standalone preview.
Detailed work items live in the repository’s tbd beads.

## Release Candidate

Repository-local preview work is complete.
The first public release is `v0.2.0`, tracked by `mp-yxay`. After the upgrade and
release-preparation pull requests merge:

- confirm the PyPI pending publisher matches the `publish.yml` workflow and `pypi`
  environment;
- create the `v0.2.0` GitHub release from the validated `main` commit using the
  checked-in release notes;
- watch the trusted-publishing workflow to completion; and
- verify the PyPI metadata, artifacts, and isolated installed-package smoke tests.

## Completed Preview Scope

- Standalone package metadata, AGPL licensing, dependency locks, and optional cloud
  scope
- Public-hygiene, artifact-content, secret, link, frontmatter, format, lint, type, test,
  dependency-audit, and Python-version-matrix gates
- Deterministic offline smoke coverage and clean installed-wheel validation
- Package-local development, documentation, browser assets, Agent Skill, CI, and
  trusted-publishing workflow
- Synthetic examples and operator, architecture, installation, development, migration,
  release, security, and supply-chain guidance

## Post-Preview

- Improve plugin compatibility diagnostics and lifecycle guarantees.
- Add interactive manual-step helpers after the workflow contract has repeated in real
  use.
- Expand observability and structured invocation records.
- Evaluate additional cloud providers and orchestration backends.
- Add performance benchmarks with deterministic public fixtures.
- Stabilize extension APIs only after downstream usage provides compatibility evidence.
