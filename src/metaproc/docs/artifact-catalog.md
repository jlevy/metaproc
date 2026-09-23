# Metaproc Artifact Catalog

Every artifact metaproc writes to, or reads from, a run directory.
Each entry names the filename pattern, path, format, schema, lifecycle, writer, and
readers. Format choices follow
[conventions.md §File Format Policy](conventions.md#file-format-policy); the
run-directory layout is described in
[conventions.md §Harness-Owned Runtime Artifacts](conventions.md#harness-owned-runtime-artifacts).

Use this catalog when adding a new artifact, renaming one, or auditing format
consistency. The companion programmatic registry is `src/metaproc/paths.py`, which holds
the filename constants.

Each section below is also a write contract, and the contract is what decides how the
artifact may be written.
State artifacts and structured documents are *published*: staged in the destination
directory and committed in one step, so a reader sees the previous file or the complete
new one. Stream artifacts are *appended*, and plain-text captures taken from a running
subprocess are *live streams* — both would be weakened by replacement, not strengthened.
See
[arch-file-io-utilities.md § Write Contracts](arch-file-io-utilities.md#write-contracts)
for the helper that expresses each one and for the check that enforces it.

## Summary by format

| Format | Count | Where it lives |
| --- | --- | --- |
| YAML | ~19 | `<run>/.state/` |
| JSONL | ~9 | `<run>/.logs/` |
| JSON | 3 writers | `<run>/.state/` sidecars, `<run>/resources.json`, arena cache |
| Softschema MD | 6 | `<run>/` and `<run>/<artifact-tree>/` (post-run human reports) |
| Plain text | ~5 | `<run>/.logs/` (subprocess captures and prompt snapshots) |

## State artifacts (YAML)

Durable engine bookkeeping under `<run>/.state/`. Machine-internal records; agents must
not hand-edit them. Every one is published atomically through `metaproc.io`’s
`atomic_write_text` / `atomic_output_file`; none uses `backup_suffix`, because a backup
makes the destination briefly absent and several of these are polled continuously.

| Filename | Path | Schema (Pydantic) | Lifecycle | Writer | Primary readers |
| --- | --- | --- | --- | --- | --- |
| `run-config.yaml` | `<run>/.state/` | ad-hoc outer dict with typed `ResourceRunSnapshot` resources block | atomic at creation; the launch-config fields (`variables`, `step_variants`, `variant`, `execution_profile`, `artifact_namespace`, `resolved_profiles`, `backend`, `git_sha`) rewritten by a resume that changes them, after the change is recorded; every other field keeps its creation value, and a resume from a directory other than the recorded `run_dir` is refused, since result records are anchored to it; recorded `step_variants` re-applied on a resume without `--step-variant` | `commands/run_process.py:_write_run_config` | engine resume validation, terminal resource finalizer, operations summary, metabrowser, `metaproc status` |
| `run-plan.yaml` | `<scope>/.state/` | pure-YAML `RunPlanSnapshot` (`metaproc:RunPlanSnapshot/0.1`) | atomic, refreshed before evaluating the root or nested process scope and after runtime discovery of an upstream-produced fan-out source; records step identity, shape, canonical mapped item keys, output declarations, and fingerprints while excluding item payloads and execution configuration | `commands/run_process.py:_publish_run_plan`, `_refresh_run_plan_item_keys` | runtime task/output projection, metabrowser, operator inspection |
| `input-bindings.yaml` | `<scope>/.state/` | pure-YAML `InputBindingsRecord` (`metaproc:InputBindings/0.1`) | atomic; written when a scope that binds an input `on_change: new_run` is first entered (the root by `run-process` at creation, a composite child scope when the orchestrator first prepares it), compared on every later entry before anything is written, and rewritten only when a launch adopts or releases a binding; a launch that resolves another value for a bound input is refused | `commands/run_process.py:_write_run_config`, `_enter_scope_input_bindings`, `_rewrite_input_bindings` | engine resume validation (`engine/input_bindings.py`), `run-step` and `run-parallel` (`commands/helpers.py:refuse_changed_input_bindings`), operator inspection |
| `resource-usage-summary.v1.schema.yaml` | `<run>/.state/schemas/` | compiled SoftSchema JSON Schema | atomic, terminal/recovery refresh | `engine/resource_summary.py` | SoftSchema validators, operator audit |
| `agent-operations-summary.v1.schema.yaml` | `<run>/.state/schemas/` | compiled SoftSchema JSON Schema | atomic, at terminal finalization, status-triggered recovery, and `operations summary --write` | `engine/operations_summary.py` | SoftSchema validators, operator audit |
| `process-status.yaml` | `<run>/.state/` | ad-hoc dict (typed envelope pending) | atomic, rewritten each DAG tick | `commands/run_process.py:_write_process_status` | human, `metaproc status`, metabrowser |
| `orchestrator-lease.yaml` | `<run>/.state/` | ad-hoc dict | heartbeat-updated every 30s | `io/orchestrator_lease.py:acquire_lease` | engine lease check |
| `overrides.yaml` | `<run>/.state/` | `OverridesDocument` (`metaproc:OverridesDocument/0.1`) | atomic, on `metaproc override` | `io/overrides.py:_write_overrides` | `_verify_ancestors`, `metaproc status` footer |
| `status.yaml` (per-task) | `<run>/.state/tasks/<step>/<item>/` | `StatusRecord` | atomic, on each transition | `io/state_io.py:write_status_at` | engine, CLI status, metabrowser |
| `attempt.yaml` (legacy latest-launch snapshot) | `<run>/.state/tasks/<step>/<item>/` | `AttemptRecord` | atomic, replaced on launch | `io/state_io.py:write_attempt_at` | compatibility readers, operator inspection |
| `attempt.yaml` (per-attempt fact) | `<run>/.state/tasks/<step>/<item>/attempts/<attempt-id>/` | `TaskAttemptRecord` (`metaproc:TaskAttemptRecord/0.1`) | atomic before launch; one terminal update after attempt-owned validation | `io/state_io.py:start_attempt_at`, transition helpers | replay, scheduler, operator inspection |
| `accepted-anomalies.yaml` (per-attempt evidence) | `<run>/.state/tasks/<step>/<item>/attempts/<attempt-id>/` | `TaskAttemptAnomaliesRecord` (`metaproc:TaskAttemptAnomalies/0.1`) | atomic before the successful terminal attempt update; absent for clean and unsuccessful attempts | `io/state_io.py:end_attempt_at` | attempt-history projection, replay, operator inspection |
| `result.yaml` (per-task) | `<run>/.state/tasks/<step>/<item>/` | `ResultRecord` | atomic, once at completion | `io/state_io.py:write_result_at` | engine, downstream steps |
| `manual-ack.yaml` (per-task) | `<run>/.state/tasks/<step>/<item>/` | `ManualAckRecord` | atomic, on operator command | `io/state_io.py:write_manual_ack_at` | engine |
| `collected-inputs.yaml` (per-step) | `<run>/.state/tasks/<step>/` | `CollectedInputsRecord` (`metaproc:CollectedInputs/0.1`) | atomic, each time the step is handed its `collect:` documents, rewritten only when its contents change; digest of each collected document’s outcome projection | `io/state_io.py:write_collected_inputs_at` | engine resume reuse check (`engine/collected_inputs.py:changed_collected_inputs`) |
| `runpool-status.yaml` | `<run>/.state/` or `<run>/.state/steps/<step>/` | `RunPoolStatus` | atomic, rewritten each tick | `runpool/status.py:write_status` | human, `metaproc pool`, metabrowser |
| `scale-state.yaml` | `<run>/.state/` or `<run>/.state/steps/<step>/` | `ScaleState` | atomic, each tick | `runpool/status.py:write_scale_state` | engine controller on reconnect |
| `scale-override.yaml` | `<run>/.state/` or `<run>/.state/steps/<step>/` | `ScaleOverride` | atomic, on operator command | `runpool/status.py:write_scale_override` | engine controller |
| `dispatch-manifest.yaml` | `<run>/.state/steps/<step>/` | ad-hoc dict (typed envelope pending) | atomic, once after dispatch (appendable) | `io/dispatch_manifest.py:write_dispatch_manifest` | engine on resume |
| `claimed-items.yaml` | `<run>/.state/steps/<step>/worker-<id>/` | `ClaimedItemsRecord` | atomic, on each claim | `io/claimed_items.py:write_claimed_items` | engine claim coordinator |
| `runpool-status.yaml` (worker-scoped) | `<run>/.state/workers/worker-<id>/` | `RunPoolStatus` | atomic, rewritten each tick | `runpool/status.py:write_status` | human, `metaproc pool`, metabrowser |
| `pool-kill-requested.yaml` | `<run>/.state/` or `<run>/.state/steps/<step>/` | ad-hoc dict | atomic, once | `runpool/kill.py:_write_sentinel` | engine pool loop |

## Stream artifacts (JSONL)

Append-only operational streams under `<run>/.logs/`. Line-recoverable, parseable in
chunks. Gzip-passthrough (`.jsonl.gz`) supported via metaproc’s gz-aware readers; the
logical type stays `.jsonl`.

| Filename | Path | Schema (Pydantic) | Writer | Primary readers |
| --- | --- | --- | --- | --- |
| `process-events.jsonl` | `<run>/.logs/` | `ProcessEvent` (discriminated union) | `runpool/process_events.py:ProcessEventLogger._write` | trace builder, resource joiner, metabrowser process-log view |
| `events.jsonl` (run-owned or per-step) | `<run>/.logs/runpool/` or `<run>/.logs/runpool/steps/<step>/` | `RunPoolEvent` (discriminated union) | `runpool/events.py:EventLogger._write` | trace builder, auth-usage aggregator, operator inspection |
| `events.jsonl` (per-worker) | `<run>/.logs/runpool/workers/<worker-id>/` | `RunPoolEvent` (discriminated union) | `runpool/events.py:EventLogger` | same as above |
| `health.jsonl` (run-owned, per-step, or per-worker) | `<run>/.logs/runpool/...` | `HealthSampleEvent` | `runpool/events.py:EventLogger` (health channel) | `metaproc pool health`, operator triage |
| `dispatch-config-changes.jsonl` | `<run>/.logs/`; a composite child scope’s input-binding changes in `<scope>/.logs/` | ad-hoc dict (typed envelope pending): `dispatch_config_change` and `launch_config_change` events | `commands/run_process.py:_record_resume_config_change`, `_record_launch_config_changes`, `_enter_scope_input_bindings` | `metaproc auth usage` resume timeline (`commands/auth.py`) |
| `trace.jsonl` | `<run>/.logs/derived/` | `TraceEvent` | `trace/store.py:write_trace` | metabrowser trace view, `metaproc trace` |
| `<step>_<context>_<ts>.jsonl` | `<run>/.logs/tasks/<step>/<item>/` | depends on agent adapter | `runpool/backend.py` (subprocess stdout capture) | trace extractor, human debugging |
| `<session-stem>.codex-sessions/YYYY/MM/DD/rollout-*.jsonl[.zst]` | `<run>/.logs/native/<step>[/<item>]/` | Codex CLI native rollout (externally owned) | `dispatch/slot_coordinator.py:SlotCoordinator.preserve_native_session_logs`; complete set staged privately and atomically published before slot teardown | explicit native-session tooling, human debugging |
| `<session-stem>.claude-projects/<project>/**/*.jsonl` and `*.meta.json` | `<run>/.logs/native/<step>[/<item>]/`, when `no_session_persistence: false` | Claude Code native transcript (externally owned) | `dispatch/slot_coordinator.py:SlotCoordinator.preserve_native_session_logs`; complete set staged privately and atomically published before slot teardown | explicit native-session tooling, human debugging |
| `invocations.jsonl` | `<run>/.logs/tools/<tool-name>/` | Tool-specific record on read side; write side currently ad-hoc | consumer plugin | resource joiner, eval judge, usage aggregator |
| `web-searches.jsonl` | `<run>/.logs/tools/<tool-name>/` | Consumer-defined search log | consumer plugin | eval judge, human debugging |
| `resource-events.jsonl` | `<run>/.logs/` | `ResourceEvent` (discriminated union) | `logutil/resource_events.py:ResourceEventLogger.write` plus atomic rewrite by rollup | resource rollup builder |

Native CLI session records are private, externally owned source evidence.
They can contain full prompts and responses, tool inputs and outputs, and file contents
read by the agent. Their mode-0700 directories and mode-0600 files follow the ordinary
`.logs/` lifecycle: operational, potentially large, gitignored, and safe to delete.
They are not promoted into the durable declared-artifact tree or included in sharing or
export by default. A missing native set means either the CLI emitted no matching record
or best-effort preservation failed; readers must not infer that no agent activity
occurred.

Legacy: `runpool-events.jsonl` is the pre-V2 equivalent of `events.jsonl`. Still parsed
by the trace extractor as a fallback; new runs do not emit it.

## Structured documents (JSON)

| Filename | Path | Schema (Pydantic) | Lifecycle | Writer | Primary readers |
| --- | --- | --- | --- | --- | --- |
| `resources.json` | `<run>/` | strict standalone `ResourcesDocument` (`metaproc:ResourcesDocument/0.1`; historical V1/V2 readable) | atomic at terminal finalization or inactive recovery | `engine/resource_rollup.py:write_resource_artifacts` | metabrowser `/api/resources`, `metaproc resource-report`, SoftSchema validators |
| `*.invocation.json` (sidecar) | `<run>/.state/tasks/<step>/<item>/<attempt>/` | ad-hoc dict | atomic, once before spawn | `runpool/backend.py:write_invocation_sidecar` | trace claude_agent extractor, human debugging |
| tool cache `*.json` | `<run>/.logs/tools/<tool-name>/cache/...` (typical) | ad-hoc (externally-owned upstream payload) | atomic, once per cache miss | consumer plugin | tool wrapper on re-run |

JSON is reserved for deeply-nested / large machine documents (`resources.json`) and
externally-owned payloads (arena cache).
The invocation sidecar is misaligned with the policy and is planned to convert to
`*.invocation.yaml` — see **Pending renames** below.

## Human reports (softschema MD)

YAML frontmatter (typed envelope) plus markdown body.
Generated post-run for operator consumption.
Pattern documented in the standalone
[softschema-guide.md](https://github.com/jlevy/softschema/blob/main/docs/softschema-guide.md)
(or `softschema docs guide` locally) and in
[conventions.md §Frontmatter Document Model](conventions.md#frontmatter-document-model).

| Filename | Path | Envelope key + schema | Writer | Primary readers |
| --- | --- | --- | --- | --- |
| `usage.md` | `<run>/` | `usage` / `metaproc:UsageReport/0.2` | `commands/write_usage.py` via `logutil/usage.py:write_usage_report` | human operator |
| `resource-usage-summary.md` | `<run>/` | `resource_usage` / `metaproc.resources:ResourceUsageSummary/v1` | `engine/resource_summary.py` | human operator, SoftSchema validation |
| `operations-summary.md` | `<run>/` | `agent_operations` / `metaproc.operations:AgentOperationsSummary/v1` | `engine/operations_summary.py`, after resource finalization in `commands/run_process.py`, or when missing after status-triggered resource recovery in `commands/status.py` | human operator, `metaproc operations rollup`, SoftSchema validation |
| `qa-report.md` (per-item) | `<run>/<artifact-tree>/.../` | `qa` / domain-defined | downstream QA plugin handler | human operator |
| `qa-summary.md` (per-process) | `<run>/<artifact-tree>/.../` | `qa_summary` / domain-defined | downstream QA plugin handler | human operator |

The `usage.md` envelope is registered in `metaproc.io.frontmatter.ENVELOPE_MAP`; the
`qa` / `qa_summary` envelopes are registered the same way.

## Plain text captures

| Filename | Path | Writer | Notes |
| --- | --- | --- | --- |
| `process_<attempt_id>.log` | `<run>/.logs/tasks/<step>/` or `<run>/.logs/tasks/<step>/<item>/` | `runpool/backend.py` | Live stream: the fd is handed to the child, so an operator can tail it while the step runs. Gzip on close |
| `probe.stderr` | `<run>/.state/steps/<step>/...` | `dispatch/pool_dispatch.py` | Captured stderr from a failed preflight probe |
| `prompt-<step>-attempt<N>-<HHMMSS>.txt` | `<run>/.logs/tasks/<step>/` | `commands/run_process.py:_execute_agent_step` | Resolved prompt for one scalar agent attempt; atomic, once before launch |
| `<step>_<context>_<ts>-attempt<N>.prompt.md` | `<run>/.logs/tasks/<step>/<item>/` | `commands/run_parallel.py:_build_prepare_launch` | Resolved prompt for one fan-out agent attempt; atomic, once before launch |
| `prompt-<step>-<context>-<HHMMSS>.txt` | `<run>/.logs/tasks/<step>/` or `<run>/.logs/tasks/<step>/<item>/` | `commands/run_step.py` or `engine/runtime.py:launch_step` | Resolved prompt for a direct `run-step` launch; atomic, once before launch |

## Credential files

Written outside the run directory, into an agent CLI’s own configuration location or a
per-dispatch slot. Published through `metaproc.io.write_secret_text`, which creates the
staged file at mode `0o600` before the first byte lands and commits it by rename, so the
file is never readable beyond its owner at any instant.

| Filename | Path | Writer | Notes |
| --- | --- | --- | --- |
| `.credentials.json` | `~/.claude/` or `<slot_dir>/` | `adapters/claude_cli.py` | Claude Code OAuth blob |
| `.claude.json` | `<slot_dir>/` | `adapters/claude_cli.py` | Onboarding marker |
| `settings.json` | `<slot_dir>/` | `adapters/claude_cli.py` | Sandbox network allowlist |
| `auth.json` | `~/.codex/` or `<slot_dir>/.codex/` | `adapters/codex_cli.py` | Codex CLI credential |
| `config.toml` | `<slot_dir>/.codex/` | `adapters/codex_cli.py` | Pins the credential store; a truncated copy would let codex fall back to the OS keychain |
| `models.json` | `~/.pi/agent/` | `cloud/gcp/container_bootstrap.py` | May carry a literal provider key |

## Pending renames

Tracked per the [file-format policy](conventions.md):

| Current | Planned | Reason |
| --- | --- | --- |
| `*.invocation.json` | `*.invocation.yaml` | State-shaped sidecar; YAML matches the surrounding `.state/` convention. |

The earlier paired rename proposal for `resources.json` and `usage.md` was superseded by
the additive `resource-usage-summary.md` artifact.
Existing filenames remain stable.

Pending envelope/schema hygiene (separate plan, listed for completeness):

- Typed Pydantic envelopes for `run-config.yaml`, `process-status.yaml`,
  `dispatch-manifest.yaml`, `dispatch-config-changes.jsonl`.
- Typed Pydantic discriminated unions for `events.jsonl` and `health.jsonl`.
- Symmetrize arena `invocations.jsonl` writer to use the same Pydantic model the reader
  enforces.

## Companion references

- [conventions.md §File Format Policy](conventions.md#file-format-policy) — when to pick
  which format.
- [conventions.md §Harness-Owned Runtime Artifacts](conventions.md#harness-owned-runtime-artifacts)
  — the three-branch run-directory layout.
- `src/metaproc/paths.py` — programmatic filename registry.
- `src/metaproc/io/frontmatter.py` — `ENVELOPE_MAP` and softschema auto-detection.
- [metaproc-operator-reference.md](metaproc-operator-reference.md) — operator-facing
  commands.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
