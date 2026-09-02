# Changelog

All notable user-facing changes are recorded here.

This project uses [Semantic Versioning](https://semver.org/) while it is in the 0.x
development series.

## [Unreleased][unreleased]

### Documentation

- The core documentation set now ships inside the package.
  `metaproc help` serves 17 topics instead of 3, covering the design doc, the
  conventions, the artifact catalog, the general framework model, the credential and
  cloud-dispatch runbooks, and all seven architecture documents, so a consumer reading
  from an installed wheel has the same documentation as a reader of the repository.
  `metaproc help` with no topic lists them in reading order with approximate sizes.
- `metaproc help developer` and the Agent Skill catalog list the full topic set.
  Regenerate committed skill copies with `metaproc skill metaproc --install`.
- Documentation paths moved: `docs/arch/` is gone, its contents now in
  `src/metaproc/docs/`; `arch-metaproc-core.md` is `metaproc-design.md`;
  `docs/releases/` is `docs/project/releases/`. Project-internal material (revision
  histories, future-work backlogs) moved under `docs/project/design/`.
- New `devtools/check_shipped_links.py` gate: a relative link in a shipped document must
  resolve inside `src/metaproc/`, so links that are valid in a checkout but dead in the
  wheel fail CI.
- New `devtools/check_doc_dates.py` gate: a shipped document’s `last updated` date must
  not be older than its most recent substantive commit.
  Reflows are ignored, so `make format` does not invalidate every date at once.

These documentation changes altered no runtime behavior, artifact shape, or CLI flag.

### Added

- **Mapped composite scopes**: a `mode: composite` step may now declare `for_each` and
  run one child process scope per item in-process under `<run>/<step>/<item-key>/`. All
  scopes share the parent run execution context and executable-leaf ceiling; each parent
  item records durable status, attempt history, validated outputs, and a result.
  Each scope binds one canonical identity to its run directory, template variables, task
  state, and child event stream, so a fixed child output path remains isolated per item.
  Dot-only item keys are rejected before path construction.
  Scope evaluation defaults to a bounded concurrency of 32 and waits for siblings to
  finish after an ordinary item exception.
  Cancellation terminally records the affected parent attempt, and mapped items emit
  start, completion, and failure events.
  The first implementation is single-host: `gcp-worker` partitioning and whole-scope
  `for_each.retry` are rejected, while retries remain available on child leaves.
  Unsupported mapped-worker topology is rejected before any DAG step or cloud dispatch
  starts.

### Fixed

- **An agent’s own verdict outranks its exit code**: some adapters write every declared
  output, emit a terminal success record, and then exit nonzero while shutting down.
  A scalar agent step whose agent reported success and whose declared outputs all
  validate now completes instead of failing, and the exit code is retained as an
  accepted anomaly as attempt-owned, versioned evidence rather than discarding it.
  The in-memory attempt projection exposes that evidence through `anomalies`, while the
  existing strict `metaproc:TaskAttemptRecord/0.1` payload stays readable by prior
  releases. Readers accept the short-lived pre-release inline form so those runs remain
  usable. The override is narrow by design: it never applies to a step with no declared
  outputs, to an agent that did not claim success, or to a process the supervising
  RunPool killed. Startup banners are also no longer mistaken for failure causes — a
  terminal `result` record is consulted before the last-non-JSON-line fallback, and that
  fallback now only considers lines written after the last structured record, so a
  terminal-capability notice printed at startup can no longer be reported as the reason
  a step failed.

- **Agent transcripts are free of terminal styling**: all four agent launch paths, and
  the probe paths that launch an adapter CLI and parse its output, now seed the child
  environment with `NO_COLOR`, `FORCE_COLOR=0`, `CLICOLOR`/`CLICOLOR_FORCE` off, and
  `TERM=dumb`. A step’s authored `env` block is still applied afterward, so an explicit
  override remains possible.
  ANSI sequences in a JSONL transcript have no reader and were corrupting failure
  classification and tool-use probes.

- **A raw path an upstream step writes is execution state, not authored input**:
  `produced_refs` now covers a raw `prompt_paths` or `uses` entry whose exact path a
  step this one depends on declares as an output, so its bytes leave the step
  fingerprint the way a dep-ref’s already did and the plan can be published before the
  run produces the file.
  The match is keyed through `normalize_path_key`, so a doubled slash or a `./` segment
  no longer decides the question.
  Only transitive dependency ancestors count: a step that declares the same output path
  while running independently or downstream supplies nothing to the reader, and a path
  two upstream steps both declare is refused at plan time rather than resolved
  arbitrarily.

- **Fan-out status totals come from the recorded plan**: `run-process` fan-outs have no
  `progress.md`, which belongs to the legacy `run-parallel` surface, so `status` treated
  the number of tasks observed so far as the total and reported `pending` as zero while
  dispatch was still in progress.
  It now reads the per-step item roster from the recorded run plan when no legacy items
  file exists.

- **The Gemini adapter honors a configured working directory**: `working_directory` is
  now an accepted config key and is returned to the launcher instead of `None`, so a
  Gemini step runs where its process configuration says it should.

- **GCP runs require explicit storage posture**: `metaproc gcp run` now rejects its
  default Filestore placement when `METAPROC_GCP_FILESTORE_SERVER` is unset, before
  uploading artifacts or dispatching a job.
  Callers that intentionally want ephemeral task storage must pass `--no-filestore`.

- **GCP dispatch artifacts are immutable**: `metaproc gcp run` validates its artifact
  identity before packaging and creates wheel and workspace objects only when their GCS
  names are unused, so a later dispatch cannot replace bytes referenced by an existing
  job. Re-running a dispatch that failed after uploading stays possible: an existing
  object with an identical digest is accepted as already uploaded, while one with
  different content fails with the object name and the instruction to pick a new
  `--job-name`.

- **Gemini prompt transport ignores workspace ignore rules**: the Gemini CLI adapter
  keeps its durable audit prompt while streaming those bytes through stdin, so an
  ignored prompt path cannot block or distort prompt delivery.

- **Static validation resolves defaulted process inputs**: handler and header checks now
  resolve declared process-input defaults instead of reporting false missing-input
  failures.

- **Visualization projections preserve authored and resolved fields**: `VizModel` now
  includes public process outputs, complete process-input and default declarations, and
  the resource, failure, execution-profile, namespace, and fan-out fields carried by a
  resolved plan. The MetaBrowser process and step panels render these fields, and
  projection tests enforce field parity with the authored and resolved source models.
  When a run directory is supplied, the same model includes a rebuildable view of
  scalar, mapped, and nested task records.
  A result is consumable only when it names the latest successful attempt, matches the
  current step fingerprint and declared output port, and resolves to an available
  artifact of the declared kind.
  Historical unbound, stale, missing, undeclared, and external outputs remain visible as
  diagnostics. Hydrated runs safely rebase portable paths to the local run root; no
  additional runtime ledger is written.

- **GCP credentials hydrate inside the container**: Batch job specifications now carry
  Secret Manager references rather than plaintext-expanded `secret_variables`, require
  an explicit runtime service account, reject registered credentials passed through
  `--env` or plugin bootstrap variables, and retry transient startup failures.
  Roll out the hydration-capable agent image before its dispatcher; a stale image cannot
  interpret the reference-only contract.

- **GCP artifact upload honors the selected project**: `gcp run` now passes its required
  `METAPROC_GCP_PROJECT` through wheel and workspace uploads, so service-account ADC
  without an embedded project ID can dispatch normally.

- **Fan-in failure propagation across dependency diamonds**: `require: finished` now
  tolerates failure only for affected direct dependencies collected with that policy.
  A separate success-requiring path from the same failure still blocks the consumer,
  while an unaffected required dependency does not erase the tolerant collection.

- **Resume rejects changed resolved inputs**: an existing run now compares the persisted
  resolved-variable mapping with the new launch before reusing task state.
  Only known equivalent Filestore aliases for `RUNS_DIR` normalize across local and
  cloud topology; mismatch errors list field names without exposing their values.

- **Duplicate fan-out keys fail before execution**: item discovery now rejects two
  source rows that resolve to the same `for_each.key` before either can write the shared
  task, log, output, or child-scope namespace.

- **Cloud authentication policy propagation**: `run-process --cloud` now carries the
  complete authentication-pool configuration as one typed value through orchestrator
  dispatch. Selection policy and future fields can no longer be silently dropped while
  neighboring authentication flags continue to reach the cloud job.

- **Filesystem status fails closed**: `status` and `pool status` reject a nonexistent
  local run directory instead of projecting an empty tree as complete or healthy.

- **Live ownership outranks carried terminal status**: a resumed run writes a fresh
  process projection when recursive evaluation begins, and `status`, `wait`, and
  completion checks no longer report a prior failure or cancellation as terminal while
  an orchestrator still owns the run.

- **Cloud identity and orchestrator admission remain explicit**: a mounted Filestore
  preserves attached-identity ADC precedence on persistent GCP hosts, and full-cloud
  dispatch now supplies its own `METAPROC_GCP_ORCHESTRATOR` admission marker instead of
  depending only on `BATCH_TASK_INDEX`.

- **Attempt finalization survives auth-pool teardown failure**: a credential teardown
  exception records the affected attempt as lost before propagating the operational
  error.

- **Terminal paths retain owned capacity until cleanup finishes**: local scalar agent
  launches now reuse the local launch backend and drain late launches before returning.
  On completion, timeout, or cancellation, agent and code-command process groups are
  terminated, stubborn descendants are killed, and log filters are flushed before run
  slots or host admission are released.
  Late credential leases are likewise torn down before credential capacity is released.
  The local backend now treats an explicit `PreparedLaunch.env` as the complete child
  environment, so credential variables scrubbed by an adapter cannot leak back in from
  the Metaproc process.
  Cleanup after an exited leader is fenced by process identity, cleanup failures are
  reported without replacing the command result or cancelling the remaining shutdown
  work. Ctrl-C follows cooperative asyncio cancellation; SIGTERM retains the hard
  descendant reaper for externally terminated orchestrators.
  Forced RunPool shutdown gives cancelled futures a terminal attempt and credential
  outcome, suppresses retry churn, and drains queued and late-launching submissions so
  work cannot start after the pool has closed.
  That drain is bounded; final status, events, health state, and log closure still run
  if backend cleanup wedges.
  Descendant tracking is pruned to live identities, late group members are discovered
  after leader exit, and sampled commands fence both leader and group identity before
  signalling. Active or failed work outranks carried cancellation when deriving process
  status, so a later partial run does not remain falsely cancelled.
  Long-running Python handlers can observe that request through
  `StepContext.cancel_requested()`.

### Changed

- **Agent CLI runtime pins**: the exact adapter contracts now target Claude Code
  2.1.234, Codex 0.147.0, and Pi 0.84.2. Each was the newest stable release outside the
  14-day package cool-off at the time of review.
  Pi’s installation hint now uses the active `@earendil-works/pi-coding-agent` package
  scope.

- **Gemini CLI runtime pin**: the local adapter, agent image, and supply-chain policy
  now agree on Gemini CLI 0.55.1, the newest stable release outside the repository’s
  14-day package cool-off at the time of review.

- **Metabrowser 0.9 and browser plugin SDK 0.5**: the optional `browser` extra now
  requires `metabrowser==0.9.0`, and the bundled Metabrowser plugin targets browser SDK
  0.5 instead of 0.1. Plugin discovery refuses a manifest declaring the wrong SDK, so
  the two move together.
  Three contract changes were carried: navigation goes through
  `mb.navigation.open({ path })` rather than the removed `mb.openPath`; the Document
  view reuses the markdown built-in’s `mountRendered`, which is what `renderRendered`
  became; and plugin assets are no longer eager tags in the page — Metabrowser loads
  them per selected kind from a declared descriptor, preserving manifest script order.
  Metaproc’s views, kinds, data hooks, and sidekick routes are unchanged, and the
  `MetaprocDomainViews.openPath` re-export, which nothing consumed, is gone.

- **Composite output boundaries are enforced**: scalar and mapped composites now
  validate every declared child-process output before completing.
  Resume revalidates those child outputs even when the mapped parent publishes only a
  subset; a missing or invalid child output makes only that item actionable again.
  Existing composite specs with inaccurate output declarations must correct or remove
  those declarations.

- **SoftSchema 0.8 structural diagnostics**: require `softschema>=0.8.0,<0.9` and map
  structural failures to its stable error `code` instead of a JSON Schema engine
  keyword. Property-level failures now resolve to the affected field in Metaproc’s
  existing `location` value.
  SoftSchema 0.8 adds a `repair` subcommand and its supporting API upstream, and always
  reports a `repairs` list on a validate result; ordinary schema verdicts are unchanged.
  `metaproc softschema repair` is unaffected and keeps routing through Metaproc’s own
  YAML repair. Supported composed and conditional schemas can therefore use
  `status: enforced` without adding a Metaproc-specific schema layer.

- **Typed cloud authentication transport**: the internal `OrchestratorDispatchConfig`
  constructor now accepts one `AuthPoolFlags` value instead of separate
  authentication-policy fields.
  This keeps the operator-to-orchestrator and orchestrator-to-worker boundaries on the
  same transport shape.

- **One credential-pool lifecycle for scalar and fan-out agents**: non-fan-out agent
  steps now lease the configured pool label, apply the same credential scope and scrub
  rules, classify failures, walk fallback labels on retry, and emit the same
  `auth_lease_acquired` and `auth_outcome` evidence as RunPool items.
  Nested leaves bind slots and event join keys to their path-relative child scope, so
  credential material stays inside the logical run tree even when a run directory is
  symlinked to another volume.
  Composite fan-out slot paths and authentication-event `run_id` values now include that
  child scope; consumers should treat the field as a run-tree path scope rather than a
  root-only identifier.
  Blocking credential storage work runs through the run-owned executor.
  Scalar quota scans run only for the blocking `refuse` posture; admission failures
  before the first launch create no attempt, while exhaustion after a retry makes the
  existing task state terminal.
  Adapter mismatches emit an explicit warning, log record, and `auth_skipped` event
  before using ambient authentication, including on worker entrypoints.

- **One execution context across recursive scopes**: local `run-process` execution now
  shares one executable-leaf ceiling across fan-out pools, scalar steps, code work, and
  composite descendants.
  Synchronous handlers and code commands use the run-owned executor, while scalar agent
  processes reuse the local launch backend without blocking the event loop.
  `--force` reaches composite descendants while root step selectors remain root-scoped.
  Command-backed code steps at the same DAG level may now run concurrently and acquire
  the shared run ceiling; fan-out paths retain their step ceilings as well.
  The executor defaults to 32 workers and grows to an explicit higher run ceiling, so
  its implementation capacity never silently reduces that ceiling.
  In the initial single-profile topology, the context also lazily owns one adaptive
  RunPool. Scalar agent leaves in every mapped child submit prepared launches to that
  pool, which supplies shared pressure response, process-tree supervision, status, and
  events. The existing run leaf ceiling and host admission remain the hard run and
  cross-run boundaries; command-backed code work retains its existing supervised path.
  Commands share the process directory, so authored steps that mutate shared files,
  repositories, or lockfiles must declare per-item paths or provide their own
  synchronization.

- **Full-cloud GCP topology is now enforced**: launching `run-process` with
  `--backend gcp-worker` from an operator host now fails unless `--cloud` is also set,
  and direct non-dry `run-parallel --backend gcp-worker` execution requires the GCP
  Batch runtime marker.
  The bare backend form remains available to the inner GCP Batch orchestrator, and dry
  runs remain available for inspection.

### Removed

- **Persistent GCP gateway compatibility**: removed `gcp remote`, `gcp remote-run`,
  `gcp self-install`, remote status routing, workstation Filestore path aliases, and the
  `METAPROC_GATEWAY_HOST` and `METAPROC_GCP_FILESTORE_REMOTE_RUNS_DIR` environment
  variables. Batch-native status and logs remain available, and filesystem-oriented
  commands now require an explicit locally visible run directory.
- **Framework-owned run archiving**: removed `gcp archive`; consumers own durable run
  publication and retention.
- **Split-tree cloud compatibility**: removed `status --cloud-runs-dir`,
  `validate --cloud-runs-dir`, and `pool retry-missing`. Hydrated and full-cloud runs
  use one run tree for state, output validation, and recovery.

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
