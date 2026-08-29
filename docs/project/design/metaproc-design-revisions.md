# Metaproc Design: Revision History

Authoring revisions of
[metaproc-design.md](../../../src/metaproc/docs/metaproc-design.md), kept as a project
record. These are not releases; for what shipped when, see
[CHANGELOG.md](../../../CHANGELOG.md) and [the release notes](../releases/).

Approximate mapping: rev2i and earlier predate v0.2.0; rev2j through rev2l correspond to
v0.2.1; rev2m and rev2n to v0.3.0; rev2o was unreleased when the history was moved here.

## Revisions

### rev2q (2026-08-28)

- Gave the run-plan snapshot a first-class home as §9.7, alongside the other
  load-bearing runtime files.
  It had been introduced only as a line in the directory listings and a prose block
  inside the MetaBrowser section, which framed a runtime contract as a browser feature.
- Recorded why the snapshot exists rather than persisting the resolved plan or
  reconstructing from the authored spec, and documented its exactness validators,
  publication and refresh points, and the parent-authorizes-child scope chain.
- Added §10.6 for consumable outputs: the ordered acceptance gates, the rejection-reason
  taxonomy, why the attempt and fingerprint gates exist, path rebasing, and typed
  coverage gaps.
- Reduced the MetaBrowser section to a consumer of that contract.

### rev2p (2026-08-26)

- Documented the existing single-host cloud placement: one `gcp run` Batch task whose
  command is a complete local-backend process.
- Distinguished that placement from multi-VM `gcp-worker` fan-out and recorded it as the
  supported cloud path for mapped composites.

### rev2o (2026-08-25)

- Documented cancellation-safe ownership for executor work, scalar credentials, local
  scalar agent process groups, and sampled code commands.
- Clarified that scalar supervision reuses the launch backend lifecycle without creating
  another pool or adaptive controller.

### rev2n (2026-08-24)

Defined `run-process --cloud` as the application-level cloud surface and `gcp run` as a
lower-level single-task primitive.
Recorded the planned `--orchestrator` and `--worker` placement interface, its immutable
resolved-topology boundary, a run-wide worker placement for the first implementation,
and the explicit transport requirement for later split-locus execution.

### rev2m (2026-08-24)

Removed the unsupported laptop-orchestrated GCP worker topology, split-tree validation
and recovery commands, and the persistent gateway command family.
The supported cloud surfaces are full-cloud `run-process --cloud`, one-shot `gcp run`,
and Batch-native monitoring and lifecycle commands; a hydrated run is one locally
visible tree.

### rev2l (2026-08-09)

Release-readiness synchronization:

- Marked `example_plugin` as a fictitious downstream namespace and removed references
  that implied its domain implementation ships with Metaproc.
- Replaced the missing QA-plan reference with the current framework boundary.
- Applied the common documentation punctuation, conjunction, and terminology rules
  across the maintained reference.

### rev2k (2026-08-03)

Focused resource observability:

- Documented the reconciled event ledger, exact provider meters, and explicit coverage.
- Added immutable launch topology/budget snapshots and reporting-only evaluation.
- Added causal terminal finalization, inactive local recovery, and the self-describing
  resource usage summary.
- Clarified that agent-CLI cost is a list estimate and provider-authoritative events are
  the only actual-cost boundary.

### rev2j (2026-08-02)

Typed run identity and cloud correlation:

- Updated cloud monitoring and dispatch summaries for readable `metaproc-run-id` and
  exact `metaproc-run-key` labels.
- Documented exact lookup, hash-verified mixed-generation jobs, the safe fully legacy
  fallback, and exact run-ID recovery in `gcp runs`.
- Documented exact local status identity resolution for process-subdirectory layouts.
- Updated the cloud monitoring-layer summary and Batch utility inventory.

### rev2i (2026-04-20)

Tool-use operational observability (the original design):

- **Section 14.7 (new)**: Tool-use Observability.
  Documents the three-source triad (tool wrapper invocation logs, pi-cli JSONL logs, and
  `native_web_search` config flag), the `ToolCallStats` / `ToolRunProfile` /
  `ProviderRateLimitStats` aggregation contract, the nine-member `FailureKind` taxonomy
  (`ok` / `malformed_args` / `tool_timeout` / `tool_error` / `help_invocation` /
  `tool_rejected` / `rate_limit_exhausted` / `adapter_dropped_call` / `unknown`), the
  cutoff-discipline invariant (runbook gap B, closed), and the native web-search
  partial-closure invariant (runbook gap A, partial).
- **Section 12.1**: adapter event-stream item now cross-refs the
  `tool_execution_start/end` and `rate_limit_event` records consumed by §14.7.
- **Section 15.1**: `UsageReport` paragraph adds pointer to the new `tool_profiles` and
  `rate_limit_stats` fields and the §14.7 contract.
- **Reading Guide**: new “Track tool-use telemetry or diagnose tool-call failures” row.

Validated 2026-04-20 by the regenerated `_mine-tech-mix-100-2026-04-06-c` usage snapshot
(`tool_profiles` frontmatter and `## Tool-use by Variant` table; see
[§14.7 Tool-Use Observability](#147-tool-use-observability)).

### rev2h (2026-04-19)

Claude Code CLI Personal-Plan auth on GCP Batch (the original design):

- **Section 12.2**: `claude-code-cli` reference adapter entry now lists three auth modes
  (API key, interactive login, Personal-Plan OAuth via Secret Manager) and the
  `strict-mcp-config` flag.
- **Section 21.2**: container bootstrap now invokes each adapter’s `bootstrap(home)`
  hook so adapters can materialize credential files (Claude Code CLI writes
  `~/.claude/.credentials.json` from `CLAUDE_CODE_CREDS_JSON`, then unsets the env var
  so it does not leak to child processes).
- **Section 21.14**: generalized from GH_TOKEN-only to the typed `SecretRefSet`
  registry; documents `SecretRef.resolve()` and the plaintext-refusal policy that
  applies uniformly to every row.
  Adding a new credential now means appending one row, with no further dispatch wiring.

Validated 2026-04-19 end-to-end via the `claude-cli-smoke-20260419-114859` single-task
smoke (5/5 records) and the `phase-2c-opus-gold-2026-04-19` Phase 2c re-dispatch (20/20
records at `max_concurrency=10`, no 429s, no laptop in the I/O path).

### rev2g (2026-04-17)

Documentation sync refresh for the current branch:

- corrected the runtime artifact model to reflect run-scoped `.logs/`, manual-step
  acknowledgments, `run-config.yaml`, and orchestrator leases
- refreshed the CLI surface and command semantics (`plan`, `deps`, `check-headers`,
  `browse`, `gcp scale`, `pool retry-missing`, `--only`)
- updated manual-step orchestration to match the implemented acknowledgment flow
- updated cloud execution sections for bundled/sparse container bootstrap, dispatch
  manifests, claim registries, scale files, and current GCP operator tooling

### rev2f (2026-04-12)

Documentation accuracy and cloud architecture clarity:

- **Section 5.4**: updated to list all 10 gcp subcommands (was listing only 4).
- **Section 8.3**: removed duplicate command listings (validate, tail, serve, compare,
  compare-matrix, write-usage appeared twice); reorganized into primary, plumbing, and
  utility groups without repetition.
- **Section 19.3**: added note clarifying that `gcp-worker` is a dispatch mode in
  `run-process`, not a registered `LaunchBackend` implementation.
- **Section 21.10**: new section -- Cloud Provider Naming and Extensibility.
  Documents why the CLI subcommand is `gcp` (not `cloud`) and how future providers would
  fit.
- **Section 21.11**: new section -- Persistent Infrastructure Decoupling.
  Documents the principle that metaproc does not depend on deployment topology.
  All infrastructure references are configuration, not hardcoded names.
- **Code**: replaced hardcoded `metaproc-browser` default in `sync-run --host` with
  `METAPROC_GATEWAY_HOST` env var (required via CLI or env, no hardcoded default).

### rev2e (2026-04-09)

Major revision to document capabilities built since rev2d:

- **Section 3.5**: revised from “No Heavy Orchestration Substrate Yet” to “Lightweight
  Orchestration Substrate” -- acknowledges DAG orchestrator and cloud execution while
  preserving the lightweight design philosophy.
- **Section 5.4**: updated execution layer (added run-process, kill, pool, gcp commands)
  and engine subsystems (added graph, process_events, cloud/gcp subsystems).
- **Section 8.1**: reframed around the 2-command execution model (run-process +
  run-step). run-parallel demoted to plumbing; cloud-dispatch removed.
- **Section 8.3**: expanded from 16 to 27 CLI entry points, organized into primary,
  plumbing, utility, GCP, and pool command groups.
- **Section 9.5**: monitoring surface expanded from 4 to 6 layers (added DAG-level and
  cloud-level monitoring).
  Browser section updated for process-log file kind.
- **Section 9.6**: new section documenting process-events.jsonl runtime artifact and
  ProcessEventLogger event types.
- **Section 11.4**: fan-out lifecycle updated with run-process DAG orchestrator path and
  two backend dispatch options (local, gcp-worker).
- **Section 14.1**: added FailureClass enum taxonomy (RATE_LIMITED, SERVER_ERROR,
  TIMEOUT, INVALID_OUTPUT, CRASH, UNKNOWN) and FailureCounts aggregation in
  RunPoolStatus.
- **Section 7.3**: mine process updated with cloud execution details (run-process
  --backend gcp-worker, single run-mine step replacing separate model steps).
- **Section 19**: new section -- Process Orchestration (run-process).
  DAG walker, step dispatch, fan-out backends, CLI flags, completion/resumability,
  process-status.yaml.
- **Section 20**: new section -- Dependency Graph (engine/graph.py).
  needs field, validate_step_graph, detect_cycles, downstream, topo_sort with parallel
  levels.
- **Section 21**: new section -- Cloud Execution Infrastructure.
  Two-tier architecture, container bootstrap, worker dispatch, worker/orchestrator
  entrypoints, GCPBatchConfig, LaunchBackend protocol, cloud monitoring.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
