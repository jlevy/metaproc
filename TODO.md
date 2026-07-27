# Metaproc Roadmap

This roadmap tracks the remaining work for the standalone preview.
Detailed work items live in the repository’s tbd beads.

## Preview Release Blockers

- Complete the public-hygiene pass over source, tests, fixtures, documentation, static
  assets, package artifacts, and reachable Git metadata.
- Finalize package metadata, license, supported Python and operating-system versions,
  optional cloud scope, and governance.
- Verify that framework behavior is independent of any consumer workflow when no plugins
  are installed.
- Publish migration guidance for consumer-owned commands, configuration, and artifact
  conventions.
- Reconcile direct runtime dependencies and optional extras from import evidence.
- Make development, lint, test, documentation, asset, and build tooling package-local.
- Add a deterministic offline smoke test for planning, validation, execution, resume,
  status, tail, trace, statistics, artifacts, and plugin discovery.
- Validate source and binary distributions from a clean checkout and confirm their
  required and forbidden contents.
- Run the complete Python version matrix and dependency, package-age, secret, link,
  frontmatter, formatting, lint, type, test, and artifact-hygiene gates in CI.
- Complete a trusted-publishing dry run before the first release.

## Preview Documentation

- Provide a synthetic quickstart and tested examples for sequential work, fan-out, and
  plugins.
- Document the supported command, process-file, artifact, adapter, plugin, and optional
  cloud surfaces.
- Consolidate present-state architecture, developer, operator, authentication, testing,
  and release guidance.
- Ensure packaged help topics are byte-equivalent to their canonical documentation.
- Add security, changelog, release-note, publishing, and supply-chain policies.

## Post-Preview

- Improve plugin compatibility diagnostics and lifecycle guarantees.
- Add interactive manual-step helpers after the workflow contract has repeated in real
  use.
- Expand observability and structured invocation records.
- Evaluate additional cloud providers and orchestration backends.
- Add performance benchmarks with deterministic public fixtures.
- Stabilize extension APIs only after downstream usage provides compatibility evidence.
