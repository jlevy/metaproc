# Changelog

All notable user-facing changes are recorded here.

This project uses [Semantic Versioning](https://semver.org/) while it is in the 0.x
development series.

## [Unreleased][unreleased]

- Prepare the standalone package for its first public release.

### Changed

- Require `softschema>=0.3.0,<0.4` (previously `>=0.1.4,<0.2`).

### Breaking

- `metaproc softschema compile` now requires `--contract CONTRACT_ID`.
  softschema 0.3 makes the contract id a required input to `compile_model`, so the
  sidecar always records the contract it was compiled for.
- The structure-report contract id is now `metaproc:StructureReport/v1`, renamed from
  `metaproc.structure_report.v1`.
  softschema 0.3 enforces a contract-id grammar (`[namespace:]Name[/version]`) that the
  old dotted form does not satisfy.
  Structure reports written by earlier versions no longer validate; regenerate them with
  `metaproc structure-report`, or update the `softschema.contract` value in place.
  All other built-in contract ids were already in the required form and are unchanged.

## [0.1.0][] - 2026-07-27

### Added

- Dependency-aware execution of Markdown process specs.
- Local, agent-CLI, and optional GCP Batch execution backends.
- Resumable run state, validation, tracing, resource reports, and RunPool controls.
- Credential-pool operations and adapter integrations.
- A packaged Metabrowser plugin and portable Agent Skill.
- Reproducible uv-based development, verification, build, and publishing workflows.

[unreleased]: https://github.com/jlevy/metaproc/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jlevy/metaproc/releases/tag/v0.1.0
