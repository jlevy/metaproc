# Metaproc Artifact Catalog

Every artifact metaproc writes to, or reads from, a run directory.
Each entry names the filename pattern, path, format, schema, lifecycle, writer, and
readers. Format choices follow
[conventions.md §File Format Policy](conventions.md#file-format-policy); the
run-directory layout is described in
[conventions.md §Harness-Owned Runtime Artifacts](conventions.md#harness-owned-runtime-artifacts).

Use this catalog when adding a new artifact, renaming one, or auditing format
consistency. The companion programmatic registry is
[`metaproc.paths`](../src/metaproc/paths.py), which holds the filename constants.

## Summary by format

| Format | Count | Where it lives |
| --- | --- | --- |
| YAML | ~16 | `<run>/.state/` |
| JSONL | ~9 | `<run>/.logs/` |
| JSON | 3 writers | `<run>/.state/` sidecars, `<run>/resources.json`, arena cache |
| Softschema MD | 5 | `<run>/` and `<run>/<artifact-tree>/` (post-run human reports) |
| Plain text | ~5 | `<run>/.logs/` (subprocess captures and prompt snapshots) |

## State artifacts (YAML)

Durable engine bookkeeping under `<run>/.state/`. Machine-internal records; agents must
not hand-edit them. Atomic writes via `strif.atomic_output_file`.

| Filename | Path | Schema (Pydantic) | Lifecycle | Writer | Primary readers |
| --- | --- | --- | --- | --- | --- |
| `run-config.yaml` | `<run>/.state/` | ad-hoc outer dict with typed `ResourceRunSnapshot` resources block | atomic, once at creation; immutable process, run-directory, and resolved-variable identity validated on resume | `commands/run_process.py:_write_run_config` | engine resume validation, terminal resource finalizer, metabrowser, `metaproc status` |
| `resource-usage-summary.v1.schema.yaml` | `<run>/.state/schemas/` | compiled SoftSchema JSON Schema | atomic, terminal/recovery refresh | `engine/resource_summary.py` | SoftSchema validators, operator audit |
| `process-status.yaml` | `<run>/.state/` | ad-hoc dict (typed envelope pending) | atomic, rewritten each DAG tick | `commands/run_process.py:_write_process_status` | human, `metaproc status`, metabrowser |
| `orchestrator-lease.yaml` | `<run>/.state/` | ad-hoc dict | heartbeat-updated every 30s | `io/orchestrator_lease.py:acquire_lease` | engine lease check |
| `overrides.yaml` | `<run>/.state/` | `OverridesDocument` (`metaproc:OverridesDocument/0.1`) | atomic, on `metaproc override` | `io/overrides.py:_write_overrides` | `_verify_ancestors`, `metaproc status` footer |
| `status.yaml` (per-task) | `<run>/.state/tasks/<step>/<item>/` | `StatusRecord` | atomic, on each transition | `io/state_io.py:write_status_at` | engine, CLI status, metabrowser |
| `attempt.yaml` (legacy latest-launch snapshot) | `<run>/.state/tasks/<step>/<item>/` | `AttemptRecord` | atomic, replaced on launch | `io/state_io.py:write_attempt_at` | compatibility readers, operator inspection |
| `attempt.yaml` (per-attempt fact) | `<run>/.state/tasks/<step>/<item>/attempts/<attempt-id>/` | `TaskAttemptRecord` (`metaproc:TaskAttemptRecord/0.1`) | atomic before launch; one terminal update after attempt-owned validation | `io/state_io.py:start_attempt_at`, transition helpers | replay, scheduler, operator inspection |
| `result.yaml` (per-task) | `<run>/.state/tasks/<step>/<item>/` | `ResultRecord` | atomic, once at completion | `io/state_io.py:write_result_at` | engine, downstream steps |
| `manual-ack.yaml` (per-task) | `<run>/.state/tasks/<step>/<item>/` | `ManualAckRecord` | atomic, on operator command | `io/state_io.py:write_manual_ack_at` | engine |
| `runpool-status.yaml` | `<run>/.state/steps/<step>/` | `RunPoolStatus` | atomic, rewritten each tick | `runpool/status.py:write_status` | human, `metaproc pool`, metabrowser |
| `scale-state.yaml` | `<run>/.state/steps/<step>/` | `ScaleState` | atomic, each tick | `runpool/status.py:write_scale_state` | engine controller on reconnect |
| `scale-override.yaml` | `<run>/.state/steps/<step>/` | `ScaleOverride` | atomic, on operator command | `runpool/status.py:write_scale_override` | engine controller |
| `dispatch-manifest.yaml` | `<run>/.state/steps/<step>/` | ad-hoc dict (typed envelope pending) | atomic, once after dispatch (appendable) | `io/dispatch_manifest.py:write_dispatch_manifest` | engine on resume |
| `claimed-items.yaml` | `<run>/.state/steps/<step>/worker-<id>/` | `ClaimedItemsRecord` | atomic, on each claim | `io/claimed_items.py:write_claimed_items` | engine claim coordinator |
| `runpool-status.yaml` (worker-scoped) | `<run>/.state/workers/worker-<id>/` | `RunPoolStatus` | atomic, rewritten each tick | `runpool/status.py:write_status` | human, `metaproc pool`, metabrowser |
| `pool-kill-requested.yaml` | `<run>/.state/steps/<step>/` | ad-hoc dict | atomic, once | `runpool/kill.py:_write_sentinel` | engine pool loop |

## Stream artifacts (JSONL)

Append-only operational streams under `<run>/.logs/`. Line-recoverable, parseable in
chunks. Gzip-passthrough (`.jsonl.gz`) supported via metaproc’s gz-aware readers; the
logical type stays `.jsonl`.

| Filename | Path | Schema (Pydantic) | Writer | Primary readers |
| --- | --- | --- | --- | --- |
| `process-events.jsonl` | `<run>/.logs/` | `ProcessEvent` (discriminated union) | `runpool/process_events.py:ProcessEventLogger._write` | trace builder, resource joiner, metabrowser process-log view |
| `events.jsonl` (per-step) | `<run>/.logs/runpool/steps/<step>/` | ad-hoc dicts (typed schema pending) | `runpool/events.py:EventLogger._write` | trace builder, auth-usage aggregator, operator inspection |
| `events.jsonl` (per-worker) | `<run>/.logs/runpool/workers/<worker-id>/` | ad-hoc dicts | `runpool/events.py:EventLogger` | same as above |
| `health.jsonl` (per-step/worker) | `<run>/.logs/runpool/...` | ad-hoc dicts (typed schema pending) | `runpool/events.py:EventLogger` (health channel) | `metaproc pool health`, operator triage |
| `dispatch-config-changes.jsonl` | `<run>/.logs/` | ad-hoc dict (typed envelope pending) | `commands/run_process.py:_record_resume_config_change` | resource aggregator timeline |
| `trace.jsonl` | `<run>/.logs/derived/` | `TraceEvent` | `trace/store.py:write_trace` | metabrowser trace view, `metaproc trace` |
| `<step>_<context>_<ts>.jsonl` | `<run>/.logs/tasks/<step>/<item>/` | depends on agent adapter | `runpool/backend.py` (subprocess stdout capture) | trace extractor, human debugging |
| `invocations.jsonl` | `<run>/.logs/tools/<tool-name>/` | Tool-specific record on read side; write side currently ad-hoc | consumer plugin | resource joiner, eval judge, usage aggregator |
| `web-searches.jsonl` | `<run>/.logs/tools/<tool-name>/` | Consumer-defined search log | consumer plugin | eval judge, human debugging |
| `resource-events.jsonl` | `<run>/.logs/` | `ResourceEvent` (discriminated union) | `logutil/resource_events.py:ResourceEventLogger.write` plus atomic rewrite by rollup | resource rollup builder |

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
| `qa-report.md` (per-item) | `<run>/<artifact-tree>/.../` | `qa` / domain-defined | downstream QA plugin handler | human operator |
| `qa-summary.md` (per-process) | `<run>/<artifact-tree>/.../` | `qa_summary` / domain-defined | downstream QA plugin handler | human operator |

The `usage.md` envelope is registered in `metaproc.io.frontmatter.ENVELOPE_MAP`; the
`qa` / `qa_summary` envelopes are registered the same way.

## Plain text captures

| Filename | Path | Writer | Notes |
| --- | --- | --- | --- |
| `process_<ts>.log` | `<run>/.logs/tasks/<step>/` or `<run>/.logs/tasks/<step>/<item>/` | `runpool/backend.py` | Captured subprocess stdout and stderr; gzip on close |
| `probe.stderr` | `<run>/.state/steps/<step>/...` | `dispatch/pool_dispatch.py` | Captured stderr from a failed preflight probe |
| `prompt-<step>-attempt<N>-<HHMMSS>.txt` | `<run>/.logs/tasks/<step>/` | `commands/run_process.py:_execute_agent_step` | Resolved prompt for one scalar agent attempt; atomic, once before launch |
| `<step>_<context>_<ts>-attempt<N>.prompt.md` | `<run>/.logs/tasks/<step>/<item>/` | `commands/run_parallel.py:_build_prepare_launch` | Resolved prompt for one fan-out agent attempt; atomic, once before launch |
| `prompt-<step>-<context>-<HHMMSS>.txt` | `<run>/.logs/tasks/<step>/` or `<run>/.logs/tasks/<step>/<item>/` | `commands/run_step.py` or `engine/runtime.py:launch_step` | Resolved prompt for a direct `run-step` launch |

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
- [`metaproc.paths`](../src/metaproc/paths.py) — programmatic filename registry.
- [`metaproc.io.frontmatter`](../src/metaproc/io/frontmatter.py) — `ENVELOPE_MAP` and
  softschema auto-detection.
- [metaproc-operator-reference.md](../src/metaproc/docs/metaproc-operator-reference.md)
  — operator-facing commands.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
