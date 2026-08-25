# Changelog

All notable user-facing changes are recorded here.

This project uses [Semantic Versioning](https://semver.org/) while it is in the 0.x
development series.

## [Unreleased][unreleased]

## [0.3.0][] - 2026-08-25

### Added

- **Durable per-attempt task history**: scalar and fan-out work managed by
  `run-process`, `run-parallel`, or waited `run-step` now writes a typed
  `metaproc:TaskAttemptRecord/0.1` before execution and finalizes it once with its
  disposition and failure class.
  Replay consumes the exact history when present and retains status-based compatibility
  for historical run trees.
  Attempt success waits for every attempt-owned validator, including the fan-out
  write-boundary check, and outputless tasks reach a durable terminal state.
  Process startup reconciles both attempt history and mutable task status: it closes
  attempts orphaned by a crash and rebuilds a missing terminal projection without
  disturbing work owned by a live step-scoped pool.
  Resume rejects status or attempt history addressed to another run, step, or item.

- **Item-aligned chains, fan-in collections, and declared retry**: process specs can now
  chain steps against the same fan-out item, collect fan-out results into a typed fan-in
  outcome, and declare retry policy in the spec.
  A resume enters a chain even when its head is already complete, rerunning the
  incomplete tasks and reusing the completed ones; `--force` remains the explicit
  operation for invalidating a step and its downstream work.

- **Actionable invalid-output retries**: agent steps append the latest structured
  validation failures to the next retry prompt, including output, failure kind, path,
  contract, invariant, location, and message.
  Fan-out and non-fan-out execution use the same feedback; transport failures never
  create or replace it.

- **Schema conform for agent-authored YAML**: a frontmatter scalar that YAML would
  resolve to the wrong type is requoted against the contract that is about to judge it,
  so a value genuinely named `1850` survives a `type: string` field.
  The contract’s own model decides what is wrong — only pydantic `string_type` errors
  are acted on — and the document’s own serializer decides how to rewrite it, so `1.10`,
  `007`, `1e3`, and `0x1F` keep their written form.
  A real type disagreement still fails.

- **Structured contract failure primitives**: validation failures now carry softschema’s
  `validator`, `path`, and `message` rather than a rendered sentence, plus a `kind` that
  subdivides `INVALID_OUTPUT` and adds `unreadable`. `validate_item_outputs_detailed`
  exposes them; `validate_item_outputs` keeps its existing string view.

- **Host admission for scalar launches, and RunPool as a library**: a `run-process`
  invocation with no `for_each` is now admitted through the same host gate as fan-out
  work, so several orchestrators on one machine account for each other instead of
  launching blind. Admission is deliberately best-effort: an unreachable gate or a wait
  timeout lets the launch proceed rather than failing a step that worked before
  admission existed. Enabled for the local backend.

- **GCP Batch dispatch hardening**: `metaproc gcp run` accepts repeatable
  `--workspace-package PATH` to install current-branch consumer packages, prints Batch
  state transitions while provisioning and executing, and emits an exact resource
  identity that `gcp status`, `gcp logs`, and `gcp cancel` can reattach to.
  Default workspace archives exclude top-level and vendored Metaproc source layouts, and
  safe in-repository symlinks are materialized as regular archive content while external
  links and directory-link cycles are rejected.

### Changed

- **Code-step outputs are no longer YAML-repaired**: `run-parallel`’s `mode: code`
  fan-out ran the frontmatter auto-repair pass over each item’s declared outputs before
  validating them, which `run-process`’s code path never did.
  Both code paths now leave the document alone, so a handler that emits unparsable
  frontmatter fails its item instead of being silently rewritten.
  Repair and conform stay scoped to agent-authored output.
  A process whose code handler was relying on the repair pass will start reporting
  `invalid_outputs`; fix the handler’s serializer rather than the artifact.

- **A Gemini CLI below the supported minimum is refused up front**: the adapter passes
  `--skip-trust`, which gemini-cli introduced in 0.40, so an older CLI failed every
  agent step partway through a run with an unexplained “Unknown arguments”.
  This was previously only a warning.
  The refusal reports the version found, the path it resolved, and the remedy; drift at
  or above the minimum stays a warning.
  A stale CLI shadowing the pinned binary on `PATH` now fails immediately instead of
  costing a whole run.

- **`cryptography` moves to 50.0.0**, retiring the audited advisory waiver for
  `GHSA-g6cj-pr64-35w5` / `CVE-2026-69247`. No advisory waiver is active in this
  release.

- **Development toolchain**: this repository now pins uv 0.12.3 and Node 24.19.0, tracks
  the `simple-modern-uv` v0.5.0 template, and installs its pinned, checksum-verified
  toolchain at agent session start.
  This affects contributors, not consumers of the published package.

### Fixed

- **Retry classification no longer depends on an artifact’s filename**: the decision to
  retry a missing output or give up on a structural mismatch was recovered by
  substring-matching a rendered error sentence that contained the artifact’s name.
  Two declared outputs missing for the same transient reason could receive opposite
  verdicts because one was named for a schema manifest.
  The decision now reads the structured failure kind.

- **Representation-only validation failures**: `date`, `datetime`, `time`, `Decimal`,
  and `UUID` values are normalized to their serialized form before the structural pass,
  so a quoted and an unquoted YAML date stop disagreeing.
  Only types with an unambiguous serialized form convert.

- **Cloud log tailing**: Cloud Logging entry datetimes are normalized to RFC3339 before
  being used as tail watermarks, fixing a log tail that could fail mid-run.
  A blocking generic run now reuses and closes one Cloud Logging client instead of
  leaking a client per poll.

- **Wheel overrides preserve the image dependency closure**: a verified Metaproc wheel
  override installs with `--no-deps`, keeping the audited dependencies and per-package
  release-cutoff exceptions baked into the image, and nested `uv` commands stay on the
  baked environment after package installation.

## [0.2.1][] - 2026-08-09

### Added

- **Self-identifying typed IDs**: `metaproc.ids` now provides registered
  `prefix-payload` allocation, validation, deterministic derivation, timestamped child
  derivation, and read compatibility for published underscore-form identities.
- **Exact GCP run correlation**: orchestrator and worker jobs retain a readable run
  label and a collision-resistant exact-identity key.
  Cloud inventory recovers the exact run ID from structured job metadata and keeps
  colliding readable labels separate.
- **Ledger-backed resource observability**: normalized runtime and agent evidence now
  drives strict hierarchical reports, provider meters, coverage gaps, reporting-only
  budgets, terminal finalization, inactive-run recovery, and CLI and Metabrowser views.
- **Code-step telemetry**: handlers and child processes launched by `run-process`,
  `run-step`, and `run-parallel` contribute CPU, memory, and lifecycle evidence to the
  root run ledger.
- **Installed version option**: `metaproc --version` reports the distribution version.

### Changed

- **Default run IDs**: generated run IDs are compact, time-ordered `run-...` typed
  identities. Process and title remain metadata instead of identity components;
  `RUN_ID_TEMPLATE` remains available for explicitly configured legacy formats.
- **Cloud run lookup**: `gcp status`, `gcp logs`, and `gcp cancel` query the exact
  identity key and recover mixed-generation, unkeyed jobs only when their structured
  `RUN_ID` verifies as the same run.
  Fully legacy runs retain the readable-label fallback.
  Local-directory status reads the immutable identity from run config rather than a
  process-directory basename or sanitized job label.
  Exact typed run IDs are not constrained by the legacy 63-character label heuristic.
- **Resource document contract**: new `resources.json` files use the registered
  `metaproc:ResourcesDocument/0.1` token.
  Strict readers for historical `metaproc.resources/v1` and `metaproc.resources/v2`
  artifacts remain available.
- **SoftSchema dependency**: Metaproc now requires `softschema>=0.6.0,<0.7` and follows
  its document terminology.
  Consumer repositories control their own dependency-source resolution.
- **Portable Agent Skill and documentation map**: generated agent-specific skill copies
  are drift-checked against the packaged skill, and the public docs route users through
  audience-oriented manuals and maintained architecture references.

### Fixed

- **Resource finalization and attribution**: inactive successful runs no longer recover
  as failed, historical refreshes write the complete report set, code-mode sampling
  excludes unrelated processes, and nested or fan-out work retains its owning node and
  item identity.
- **Tool latency**: paired Claude and Gemini tool spans derive a non-negative duration
  from valid timestamps instead of rolling up as zero.
- **Cloud source preflight**: vendored and submodule Metaproc paths are detected before
  dispatch so current-branch source changes cannot silently use image-baked code.
- **Cloud artifact contracts**: environment templates and operator docs include the
  required SHA-256 value for each downloaded wheel and workspace URI.

## [0.2.0][] - 2026-07-31

### Added

- Dependency-aware execution of Markdown process specs.
- Local, agent-CLI, and optional GCP Batch execution backends.
- Resumable run state, validation, tracing, resource reports, and RunPool controls.
- Credential-pool operations and adapter integrations.
- A packaged Metabrowser plugin and portable Agent Skill.
- Reproducible uv-based development, verification, build, and publishing workflows.

### Changed

- Require `softschema>=0.4.0,<0.5` and `frontmatter-format>=0.4.0,<0.5` (previously
  `softschema>=0.1.4,<0.2` and `frontmatter-format>=0.3.0`). See the
  [softschema 0.2.0](https://github.com/jlevy/softschema/releases/tag/v0.2.0) and
  [softschema 0.3.0](https://github.com/jlevy/softschema/releases/tag/v0.3.0),
  [softschema 0.4.0](https://github.com/jlevy/softschema/releases/tag/v0.4.0), and
  [frontmatter-format 0.4.0](https://github.com/jlevy/frontmatter-format/releases/tag/v0.4.0)
  release notes for the complete upstream migration surface.
- `metaproc softschema validate` now includes softschema’s `outcome` discriminator
  (`valid`, `invalid`, or `input_error`) alongside the existing `ok` field.
- Mapping-based YAML and frontmatter writes are deterministic and alias-free: repeated
  lists and mappings are expanded instead of emitting anchors.
  Cyclic values raise `YamlSerializationError` without replacing an existing target.

### Breaking

- `metaproc softschema compile` now requires `--contract CONTRACT_ID`. softschema 0.3
  makes the contract id a required input to `compile_model`, so the sidecar always
  records the contract it was compiled for.
- Softschema 0.2 enforces the contract-id grammar `[namespace:]Name[/version]`. This
  applies to plugin `Contract` registrations, process-spec `schema` fields, artifact
  `softschema.contract` metadata, and the `--schema` and `--contract` CLI options.
  All externally authored IDs must use the new form.
  Metaproc’s structure-report ID is now `metaproc:StructureReport/v1`, renamed from
  `metaproc.structure_report.v1`; the other built-in IDs were already valid.
- Structure reports written by earlier versions no longer validate.
  Regenerate them with `metaproc structure-report`, or update both `softschema.contract`
  and `structure_report.schema` to `metaproc:StructureReport/v1`.
- Softschema 0.3 and 0.4 restrict YAML inputs to bounded, JSON-compatible values.
  Aliases and anchors, merge keys, explicit tags, duplicate or non-string keys, unsafe
  integers, negative zero, non-finite numbers, excessive depth, and oversized inputs are
  rejected. Bare and quoted date- or timestamp-shaped scalars are accepted as strings in
  0.4; callers that need temporal objects must construct them explicitly after
  validation.
- Compiled schemas are validated offline and remote `$ref` targets are never fetched
  implicitly. Schemas consumed through Metaproc must be self-contained: use local `$defs`
  references or a registered Pydantic model instead of network-resolved references.

[unreleased]: https://github.com/jlevy/metaproc/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/jlevy/metaproc/releases/tag/v0.3.0
[0.2.1]: https://github.com/jlevy/metaproc/releases/tag/v0.2.1
[0.2.0]: https://github.com/jlevy/metaproc/releases/tag/v0.2.0

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
