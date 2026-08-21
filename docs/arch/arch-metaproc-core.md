---
title: "Architecture: Metaproc Core"
description: Implementation reference for the Metaproc framework core, including the spec format, runtime artifacts, CLI commands, adapter contract, plugin protocol, and robustness subsystems.
author: metaproc team
status: Approved
---
# Architecture: Metaproc Core

**Date:** 2026-03-23 (last updated 2026-08-09) **Status:** Approved

> **Maintenance**: This is a maintained architecture doc.
> Revise via `tbd shortcut revise-architecture-doc` (which prompts you to verify content
> against current code, then add a “Future Considerations” section).
> When you make non-trivial changes, bump the **last updated** date above.
> The full arch-doc index lives in
> [development.md § Architecture docs](../development.md#architecture-docs).
> 
> Companion docs (in `docs/arch/`): [arch-metaproc-core](arch-metaproc-core.md),
> [arch-runpool](arch-runpool.md), [arch-cloud-execution](arch-cloud-execution.md),
> [arch-authentication](arch-authentication.md),
> [arch-claude-code-harness](arch-claude-code-harness.md),
> [arch-testing](arch-testing.md).

Revision: rev2l

Implementation reference for Metaproc, covering how the conceptual model defined in
[metaproc-concepts-and-principles.md](../../src/metaproc/docs/metaproc-concepts-and-principles.md)
is realized in code: spec format, runtime artifacts, CLI commands, adapter wire formats,
plugin protocol, and robustness subsystems.
Run pool internals and cloud execution have their own arch docs (see companion links
above).

Additional reference docs: [conventions.md](../conventions.md) (naming rules),
[credential-setup.runbook.md](../runbooks/credential-setup.runbook.md) (auth),
[arch-file-io-utilities.md](arch-file-io-utilities.md) (curated `metaproc.io`
file-utility surface and frontmatter_format gotchas),
[metaproc-design-rev3-proposals.md](../metaproc-design-rev3-proposals.md) (remaining
future work).

Examples in this document use the fictitious `example_plugin` namespace to show where
consumer-owned processes, schemas, handlers, and artifacts belong.
Metaproc does not ship that package or its domain behavior.

## Scope and Imported Concepts

Terminology and principles live in
[metaproc-concepts-and-principles.md](../../src/metaproc/docs/metaproc-concepts-and-principles.md);
read it first for the definitions assumed below.
Section numbers are stable identifiers carried across revisions; numbering starts at 5
because earlier sections moved into the concepts doc and the companion arch docs.

Imported invariants:

- **Files are the step boundary.** The harness/agent contract is defined by file
  artifacts at declared paths.
- **The harness owns orchestration and state.** Step selection, dispatch, status
  transitions, retry, resume, and publication are all framework code, never agent
  reasoning.
- **Agents own only in-step reasoning.** Whatever the agent does internally is its own
  business; the contract is the file artifacts at the boundary.
- **Completion is determined by validation and harness-owned state**, not by file
  presence alone.
- **Process definitions are first-class artifacts.** They can be read, validated, and
  improved by other processes (meta-circularity).
- **Step modes are `manual | agent | code | composite`.** The framework treats all four
  uniformly for state, validation, retry, and observation.
- **Items file is the framework term** for a list-typed dep that drives a fan-out step.
  Analysis-domain code uses *roster* as a synonym; the framework does not.
- **The orchestrator is deterministic Python code.** A coding agent as the top-level
  orchestrator would introduce non-determinism, context-window limits, and
  conversational drift into the control plane.
  The reliability rationale is in the concepts doc; the implementation consequence
  (`run-process` is framework code) is what matters here.

An operator (human or outer coding agent) invokes `metaproc run-process` to start the
loop. The outer coding agent is a convenient interface for translating intent into CLI
commands, but it is not part of the control plane.
The same `run-process` command works identically whether typed by a human, invoked by
Claude Code, or launched by a cron job.

### Execution Chain by Topology

The same process spec runs identically across local, hybrid, and full cloud topologies.
The orchestrator and run pool move between machines; the adapter subprocesses at the
bottom of the stack are always the same.
See [arch-cloud-execution.md §2.2](arch-cloud-execution.md) for the full topology table.

See § 19 for orchestrator details, § 21 for cloud execution.

## 5. Implementation Inventory

The framework spans three abstraction profiles defined in
[metaproc-concepts-and-principles.md §3.4](../../src/metaproc/docs/metaproc-concepts-and-principles.md):
**core model**, **execution profile**, and **application profile**. The conceptual
definitions live there.
The inventory below lists the authored files, package subsystems, plugin layer, and
emitted runtime artifacts that realize each profile.
For the per-artifact reference (filename, format, schema, lifecycle, writer, readers),
see [artifact-catalog.md](../artifact-catalog.md); for format-selection rules, see
[conventions.md §File Format Policy](../conventions.md#file-format-policy).

### 5.1 File and Subsystem Inventory

```text
Authored layer
-------------
<node>.process.md
runbook.md
progress.md / other items files (fan-out source)
templates/

Execution layer (metaproc package)
----------------------------------
build_plan()
run-process (DAG orchestrator -- primary user command)
run-step (single step execution)
run-parallel (fan-out pool execution -- plumbing)
plan
deps
validate
check-headers
status / wait (run progress and orchestration)
kill (pool termination)
compact-logs
auth-check
auth setup / push / probe / status / list / enable / disable / check / rotate / prune / usage / doctor / quota / preflight / env  (labeled credential pool; see arch-authentication.md)
claude-auth push / show / rotate  (single-secret pre-pool surface; use the `auth` group for new deployments)
stats
write-usage
resource-report
pool status / pool events / pool retry-missing
gcp status / gcp scale / gcp logs / gcp cancel / gcp runs / gcp self-install / gcp resources / gcp filestore / gcp archive / gcp remote / gcp remote-run / gcp cleanup
tail (log viewer)
compare / compare-matrix

Engine subsystems
-----------------
graph (DAG validation, cycle detection, topological sort)
dep_state (declared-dependency runtime inference)
retry (error classification, backoff, policy resolution, failure classification)
log_compaction (adapter-aware stripping, thinking preservation)
memory_pressure (cross-platform measurement, pressure levels)
preflight (disk space, gcloud auth checks)
yaml_repair (unquoted-colon auto-fix)
discovery (resume-safe item filtering)
usage (extraction, pricing, aggregation)
process_events (structured DAG event logging)
viz (pure projection of Plan -> VizModel; browser and static SVG/HTML renderers; see MetaBrowser architecture)

Cloud subsystems (cloud/gcp/)
-----------------------------
batch_backend (GCPBatchConfig and shared Batch API utilities)
worker_dispatch (multi-VM item partitioning and dispatch)
worker_entrypoint (unified container entrypoint for workers)
orchestrator_dispatch (submit orchestrator as GCP Batch job)
orchestrator_entrypoint (orchestrator container entrypoint)
container_bootstrap (bundled/sparse repository bootstrap and environment setup)
resolve_token (GCP access token via google.auth)
gcp_credentials (service account credential management)

Plugin layer
------------
plugin protocol and registry
entry-point discovery (standard and workspace fallback)
domain envelopes, schemas, terminal statuses
compare-matrix defaults
form conventions

Emitted runtime layer
---------------------
Operator-facing commands and current file paths are summarized in
`metaproc-operator-reference.md`; the framework contract those commands read is defined
below.
A run directory has exactly three top-level branches:

{run_dir}/.state/        durable engine bookkeeping (needed for resume)
{run_dir}/.logs/         operational logs (not completion source of truth)
{run_dir}/<artifact-tree>/   user-templated artifact tree (no engine files mixed in)

Runtime terms in this layer:
  item: workflow data record or scalar supplied to a step
  task: harness-owned execution record for one step applied to one item
  attempt: one launch or retry within a task
  step runner: harness control plane that executes a step, especially a fan-out pool

Run-level state files (under {run_dir}/.state/):
  process-status.yaml
  run-config.yaml
  orchestrator-lease.yaml
  overrides.yaml             (operator escape hatches via `metaproc override`)

Per-step state (fan-out pool bookkeeping):
  {run_dir}/.state/steps/{step_id}/runpool-status.yaml
  {run_dir}/.state/steps/{step_id}/scale-state.yaml
  {run_dir}/.state/steps/{step_id}/scale-override.yaml
  {run_dir}/.state/steps/{step_id}/dispatch-manifest.yaml   (cloud fan-out)
  {run_dir}/.state/steps/{step_id}/worker-<id>/claimed-items.yaml

Per-task state (one runtime task per fan-out item, keyed by for_each.key):
  {run_dir}/.state/tasks/{step_id}/<item_key>/status.yaml
  {run_dir}/.state/tasks/{step_id}/<item_key>/attempt.yaml
  {run_dir}/.state/tasks/{step_id}/<item_key>/result.yaml
  {run_dir}/.state/tasks/{step_id}/<item_key>/manual-ack.yaml   (manual steps only)
  {run_dir}/.state/tasks/{step_id}/status.yaml                  (non-fan-out steps)

Logs use producer and writer scope rather than mirroring every `.state/` branch:
  {run_dir}/.logs/process-events.jsonl
  {run_dir}/.logs/dispatch-config-changes.jsonl
  {run_dir}/.logs/runpool/steps/{step_id}/events.jsonl
  {run_dir}/.logs/runpool/workers/{worker_id}/events.jsonl
  {run_dir}/.logs/tasks/{step_id}/<item_key>/*.jsonl
  {run_dir}/.logs/tasks/{step_id}/process_<ts>.log
  {run_dir}/.logs/tasks/{step_id}/<item_key>/process_<ts>.log
  {run_dir}/.logs/tools/<tool-name>/invocations.jsonl
  {run_dir}/.logs/derived/trace.jsonl

Application profile (via plugins)
----------------------------------
analysis-specific forms, frontmatter schemas, versioning, learn/proposal/apply rules
```

### 5.2 Communication Model

Process nodes communicate through two patterns:

**Bottom-up** communication starts with observed problems:
1. A child node sees friction, ambiguity, or repeated failure
2. The child logs it in `process-issues.md`
3. The child decides whether it is local or needs escalation
4. If cross-boundary, the parent node reviews it and decides what to change

**Top-down** communication starts with governance or structural review:
1. The parent reviews child issues, runs, and blueprints
2. The parent identifies a child-level or cross-process change
3. The parent updates a child node’s process spec, runbooks, or templates
4. The child workflow runs under the new conventions

**When to escalate:** An issue moves upward when it affects multiple child processes,
ownership is unclear, local fixes would break system conventions, it suggests a missing
parent-level subprocess, or it keeps reappearing after local fixes.

**When to keep local:** The issue is clearly owned by one child workflow, the fix does
not affect other nodes, and the ambiguity is internal to that node.

## 6. Authored Process Model

## 6.1 Envelope Convention

Every frontmatter document uses a self-identifying top-level envelope and includes a
`schema` field inside that envelope carrying a schema token
(`<module>:<ClassName>/<version>`).

Framework-registered envelope keys:

- `plan:`
- `process:`
- `progress:`
- `qa:`
- `qa_summary:`
- `usage:`

Domain packages register additional keys (e.g. `prediction:`, `retro:`, `record:`).

Runtime `.state/` files (`status.yaml`, `attempt.yaml`, `result.yaml`,
`manual-ack.yaml`, `process-status.yaml`, `run-config.yaml`, and similar harness-owned
state) are machine-internal records and do not use the envelope convention.

## 6.2 Core Process Shape

Every `*.process.md` file uses the same recursive authored shape.
There is no special public “root composite” schema.
A top-level lifecycle file and a leaf process file differ only in which `deps` they
declare and which step modes they use.

```yaml
---
process:
  name: example-workflow
  description: Analysis lifecycle -- predict, retro, learn

  defaults:
    default_adapter: claude-code-cli
    adapters:
      claude-code-cli:
        type: claude-code-cli
        config:
          model: sonnet
          tools: [Read, Write, Edit, Bash, Grep, Glob]
          timeout_s: 900

  deps:
    predict_process:
      path: "process/predict/predict.process.md"
      as: path
    retro_process:
      path: "process/retro/retro.process.md"
      as: path
    learn_process:
      path: "process/learn/learn.process.md"
      as: path

  steps:
    - id: predict
      mode: composite
      uses: deps.predict_process
      with:
        event_date: "{{event_date}}"
        run_mode: "{{run_mode}}"
        form_version: "{{form_version}}"

    - id: retro
      mode: composite
      uses: deps.retro_process
      needs: [predict]
      with:
        date: "{{event_date}}"

    - id: learn
      mode: composite
      uses: deps.learn_process
      needs: [retro]
      with:
        scope: "{{scope}}"
        form_version: "{{form_version}}"
        new_version: "{{new_version}}"
---
```

Composite recursion is ordinary dispatch, not a separate execution system.
The child process receives only the bindings explicitly passed by `with`.
Operator-supplied scalars are omitted in the example above for brevity.

## 6.3 Step Modes

### `mode: composite`

Delegates to another process declared under `deps:`. The dep’s path must end in
`.process.md`; the `uses:` reference is the only statement that the dep is a child
process.

Required fields:

- `uses` (`deps.<name>`)

Optional fields:

- `with`
- `needs`

### `mode: agent`

Runs an agent against declared inputs, outputs, prompt files, and adapter config.
Long procedure text lives in authored prompt files; the inline surface is reserved for
short bindings and per-invocation guidance.

### `mode: code`

Runs a deterministic handler or command.
This is the target mode for the reliability ratchet -- steps that start as `mode: agent`
harden to `mode: code` once the logic is well-understood and deterministic.

Required execution reference (exactly one):

- `handler` -- a file path relative to the process spec’s directory with a `:function`
  suffix (e.g., `scaffold_day.py:scaffold_day`). The engine loads the `.py` file via
  `importlib.util.spec_from_file_location` and calls the named function.
  This keeps handlers co-located with runbooks and process specs -- they travel together
  when process directories are moved or shared across codebases.
- `command` -- a shell command string, executed as a subprocess.

Handler signature: `def handler(context: dict, step_config: StepConfig) -> None`. The
context dict contains resolved input variables (same resolution as `mode: agent`). The
handler writes outputs directly; the engine records `.state/` completion markers.
If the handler raises, the step fails with the same state recording as a failed agent
step.

Dry-run mode prints the handler path (or command) and resolved inputs instead of
executing.

### `mode: manual`

Represents a human-performed step with explicit inputs, outputs, and completion
acknowledgment. The runtime does not silently skip manual steps.
A manual step is complete only after an operator acknowledgment artifact is written
under `.state/` with timestamp and operator identity.

Used for:

- approval gates
- release activation
- exception handling
- process change review

## 6.4 Artifact References

Artifact references are part of the Core Model described in section 6.5. The important
rule is that file roles are declared explicitly; the engine does not infer meaning from
filename patterns.

## 6.5 Inputs and Outputs

The authored model is file-first.
Every durable dependency is declared, typed, and named before execution.
Process-level dependencies live under `deps:`. Process-level operator bindings live
under `inputs:` with `param:` aliases.
Step-local consumers still declare `inputs:` and `outputs:` because those are the
binding points inside the DAG.

Core declaration fields:

| Field | Purpose |
| --- | --- |
| `path` | Filesystem location. Supports `{{run.dir}}`, `{{run.parent_dir}}`, and process-relative source-tree paths. |
| `as` | Closed value type: `string`, `path`, `list<T>`, or `map<K,V>`. |
| `parse` | Optional parse config when the file content is materialized into a value. |
| `role` | Closed semantic tag such as `process`, `template`, `packet`, `roster`, or `run-input`. |
| `produced_by` | Explicit producer ref when the file is written by a step in the same graph. |

Closed value types:

| Value type | Meaning |
| --- | --- |
| `string` | Scalar string value |
| `path` | Opaque path passed to a step or child process |
| `list<T>` | Ordered sequence parsed from a file or materialized by a handler |
| `map<K,V>` | Structured mapping parsed from a file or emitted by a handler |

Sources of values:

- operator-supplied bindings
- process-relative authored files in the source tree
- files produced earlier in the same run
- child process specs referenced by composite steps

Scopes are explicit:

- framework-owned names live under `run.*` and `step.*`
- domain-authored identifiers stay bare lowercase
- item-scope bindings enter through `for_each.bind` / `bind_fields`

The harness rejects raw path duplication when a named ref or dep exists.
The point of the model is to eliminate prompt-only contracts and implicit repo reads.

## 6.6 `prompt_paths`

`prompt_paths` is the ordered list of authored prompt files for an agent step.
Each file is loaded and inlined into the composed prompt.

Typical entries:

- primary runbook
- template file
- supporting context doc
- style guide or domain reference

Runtime prompt composition uses a structured envelope:

```xml
<prompt-file path="process/predict/predict-item.runbook.md">
...
</prompt-file>
```

The harness inlines every declared file, not only the first entry.

## 6.7 `for_each`

Fan-out is a single structured field.
The items-file source must be declared explicitly and must produce a list-typed value
(`list<map_item>`).

```yaml
deps:
  items:
    path: "{{run.dir}}/predict/items.md"
    as: list<map<string, string>>
    parse: {format: frontmatter-md, extract: items}

steps:
  - id: predict-item
    inputs:
      items: deps.items
    for_each:
      over: deps.items
      bind: item
      bind_fields: [item, category, event_date, cutoff_date]
      batch_size: 10
      retry:
        max_retries: 2
        initial_backoff_s: 10
        backoff_multiplier: 2.0
        max_backoff_s: 120
```

The framework owns fan-out mechanics generically; domains only provide typed items-file
documents and item fields.

## 6.8 `prompt_prefix`

Steps may include an optional `prompt_prefix` field: a template string that the harness
resolves and prepends to the agent prompt alongside `prompt_paths`. It is for short
invocation-specific bindings, not long procedure text.

```yaml
prompt_prefix: |
  Follow the runbook: {{step.prompt_path}}
  item={{item}} event_date={{event_date}} cutoff_date={{cutoff_date}}
```

The harness composes the agent invocation from:

1. resolved `prompt_prefix` template (if present)
2. loaded `prompt_paths` files
3. resolved parameter bindings from `with:` and `for_each`

Large inline blocks are a smell.
The lint rule is advisory, but anything much longer than 20 lines belongs in a prompt
file.

## 6.9 `with`

`with` is explicit parameter binding.

Primary use: bind named child-process inputs for `mode: composite`. That keeps recursion
explicit and prevents ambient inheritance across process boundaries.
Leaf steps may also use `with` to rename or document bindings, but the important
contract is at child-process boundaries.

## 6.10 Process Defaults

The core process shape stays small.
Versioned domain files, templates, and manifests are declared as dependencies, not in a
schema escape hatch.
Process defaults are for execution policy only: adapters, retry defaults, reuse policy,
and similar harness-owned settings.

## 6.11 Inheritance and Override Rules

Steps inherit from the process `defaults` block.
Per-step fields override defaults.

Rules:

- scalar fields on a step override the corresponding default
- list fields (such as `tools`) **replace**, they do not merge; to add a tool to the
  default set, the step must redeclare the full list
- `adapter` on a step **merges** with the default adapter config looked up by
  `default_adapter`
- `with` does not “inherit everything”; it is the explicit binding surface for that step

## 6.12 Template Variable Resolution

Template variables use one resolver path for plan building, runtime dispatch, and code
handlers. There is no second ad hoc resolver for special cases.

Reserved namespaces:

- `run.*`
- `step.*`

Closed framework-built-in set:

| Variable | Meaning |
| --- | --- |
| `{{run.id}}` | Framework-managed run identifier |
| `{{run.dir}}` | Output root for the current run |
| `{{run.parent_dir}}` | Parent directory containing runs |
| `{{run.variant}}` | Active adapter/model variant |
| `{{step.prompt_path}}` | Current prompt file being inlined |
| `{{step.prompt_paths}}` | Ordered prompt file set for the current step |
| `{{step.outputs_list}}` | Comma-joined resolved output paths for the current step |

Everything else is domain-authored and stays bare lowercase.
Unknown dotted prefixes are errors.
Unknown members under `run.*` or `step.*` are also errors.

If a value originates from the environment, the harness imports it into explicit runtime
context before template resolution.
The authored surface does not rely on implicit env fallback behavior.
Unresolved placeholders are hard errors except for item-scope fields deferred until
fan-out dispatch.

## 6.13 Step Fields Reference

| Field | Applies to | Purpose |
| --- | --- | --- |
| `id` | all | unique step identifier within the process |
| `mode` | all | `agent`, `code`, `manual`, or `composite` |
| `needs` | all | step IDs that must complete before this step |
| `description` | all | human-readable step summary |
| `with` | all | explicit bindings, especially at child-process boundaries |
| `uses` | composite | `deps.<name>` reference to a child process |
| `prompt_paths` | agent | ordered prompt/runbook files |
| `prompt_prefix` | agent | optional inline prompt template |
| `handler` | code | file-relative Python callable (`file.py:function`) |
| `command` | code | shell command string (subprocess) |
| `for_each` | agent, code | items-file-driven fan-out declaration |
| `inputs` | all | declared input artifact contracts |
| `outputs` | all | declared output artifact contracts |
| `output_root` | agent, code, manual | per-step output root override |
| `adapter` | agent, code | override default adapter config |
| `variant` | all | explicit variant override; decouples the artifact-namespace directory (`{{run.variant}}` in output paths) from the adapter/model that `--variant` selects for execution. Use to pin a summarizer/reviewer step to a specific artifact-namespace variant when the executor adapter would otherwise resolve `{{run.variant}}` to a different directory (e.g. a cross-variant overview that must read from the fixed `claude-cli` tree regardless of which adapter runs the step) |
| `env` | agent, code | environment variables injected into the subprocess |
| `max_budget_usd` | agent | per-step spending cap (passed to CLI as `--max-budget-usd`) |
| `token_budget` | agent | per-step token budget (`.process.md` config field, no CLI flag equivalent) |
| `reuse_policy` | all | `validated_outputs`, `exact_inputs`, or `never` |

## 7. Analysis Reference Profile

The analysis workflow is the proving ground for the framework and serves as the primary
application profile.

## 7.1 Predict Process

```yaml
---
process:
  name: predict
  description: Pre-analysis packet generation and per-item prediction

  defaults:
    default_adapter: claude-code-cli
    adapters:
      claude-code-cli:
        type: claude-code-cli
        config:
          model: sonnet
          tools: [Read, Write, Edit, Bash, Grep, Glob, WebSearch, WebFetch]
          timeout_s: 900
          output_format: stream-json
          permission_mode: bypassPermissions

  deps:
    packet_manifest:
      path: "process/predict/{{form_version}}/packet.yaml"
      as: path
    predict_runbook:
      path: "process/predict/predict-item.runbook.md"
      as: path
    items:
      path: "{{run.dir}}/predict/items.md"
      as: list<map<string, string>>
      parse: {format: frontmatter-md, extract: items}
      produced_by: scaffold-day
    research_packets:
      path: "{{run.dir}}/predict/research-packets/"
      as: path
      produced_by: generate-research-packet
    precedent:
      path: "{{run.dir}}/predict/precedent/"
      as: path
      produced_by: retrieve-precedent

  steps:
    - id: scaffold-day
      mode: code
      handler: "scaffold_day.py:scaffold_day"
      description: Materialize the shared item roster

    - id: generate-research-packet
      mode: code
      needs: [scaffold-day]
      inputs:
        items: deps.items

    - id: predict-item
      mode: agent
      needs: [scaffold-day, generate-research-packet, retrieve-precedent]
      inputs:
        items: deps.items
        packet_manifest: deps.packet_manifest
        research_packets: deps.research_packets
        precedent: deps.precedent
      for_each:
        over: items
        bind: item
        bind_fields: [item, category, event_date, report_session, cutoff_date]
        batch_size: 5
      prompt_paths:
        - deps.predict_runbook
      description: Run the full prediction packet for one item
      prompt_prefix: |
        Follow the runbook at {{step.prompt_path}}.
        item={{item}}
        event_date={{event_date}}
        cutoff_date={{cutoff_date}}
        packet={{packet_manifest}}
      outputs:
        prediction:
          path: "{{run.dir}}/predict/{{run.variant}}/{{item}}/prediction.md"

    - id: qa-check
      mode: code
      needs: [predict-item]
      handler: "example_plugin.qa.handler:check"
---
```

Notes:

- `FORM_VERSION` selects a packet manifest on disk; the process spec no longer embeds
  packet-selection metadata in frontmatter
- packet ordering and required forms are read from `packet.yaml`, not duplicated in
  `predict-item.runbook.md`
- the roster is a shared process-level dep; per-item outputs stay variant-scoped

## 7.2 Retro Process

Retro uses the same run-id and variant layout as predict, but its static templates are
declared as named deps instead of living in a prose convention table.

```yaml
deps:
  retro_template:
    path: "process/retro/{{form_version}}/retro.template.md"
    as: path
  integrity_template:
    path: "process/retro/{{form_version}}/integrity.template.md"
    as: path
  items:
    path: "{{run.dir}}/retro/items.md"
    as: list<map<string, string>>
    parse: {format: frontmatter-md, extract: items}
    produced_by: scaffold-retro
  prediction:
    path: "{{run.dir}}/predict/{{run.variant}}/{{item}}/prediction.md"
    as: path
    produced_by: predict.predict-item

steps:
  - id: predict-retro
    mode: agent
    inputs:
      items: deps.items
      retro_template: deps.retro_template
      integrity_template: deps.integrity_template
      prediction: deps.prediction
    for_each:
      over: deps.items
      bind: item
      bind_fields: [item, category, event_date]
```

Version bumps become packet/template changes on disk, not edits to process frontmatter.

## 7.3 Mine Process

Mine is the reference workload for fan-out, validation, publication, and cloud/local
topology parity. The important target-state rule is that agent steps do not write
directly into shared mutable KB state.

```yaml
---
process:
  name: mine
  description: Historic precedent research with stage / validate / publish

  deps:
    roster:
      path: "{{run.dir}}/mine/events.md"
      as: list<map<string, string>>
      parse: {format: frontmatter-md, extract: items}
      produced_by: setup-roster
    kb_index:
      path: "knowledge-base/kb-index.yaml"
      as: path
    generate_record_runbook:
      path: "process/mine/generate-record.runbook.md"
      as: path

  steps:
    - id: setup-roster
      mode: code

    - id: mine-adhoc
      mode: agent
      inputs:
        roster: deps.roster
      for_each:
        over: roster
        bind: event_id
        bind_fields: [event_id, item, period, event_date, category]
        batch_size: 50
      prompt_paths:
        - "{{deps.generate_record_runbook.path}}"
      outputs:
        candidate_record:
          path: "{{run.dir}}/mine/staged/{{event_id}}/"

    - id: validate-records
      mode: code
      needs: [mine-adhoc]

    - id: publish-kb
      mode: code
      needs: [validate-records]
---
```

The per-item agent stage writes only into its own declared run output.
Validation and publication are separate harness-owned steps.
That keeps shared KB state deterministic and makes cloud/local execution equivalent.

## 7.4 Learn Process

Learn consumes run outputs and packet manifests, then emits a candidate next packet
version. Its approval gate is explicit in the DAG rather than hidden in prose.

Representative shape:

1. aggregate retros into learn
2. sample deep retros for mechanism review
3. update the current packet performance checkpoint
4. propose form improvements
5. `manual` approval gate
6. materialize a new `packet.yaml` plus templates for the candidate version
7. compare baseline versus candidate packet

This keeps “change the form” as an authored file change on disk, not a mutation of
process frontmatter.

## 8. Resolved Plan Model

The resolved plan is a first-class runtime artifact: a computed data model, not a
markdown checkbox document.

## 8.1 Why the Plan Is Data

Resolution logic lives in one place, not scattered across commands.

- `build_plan()` computes one resolved `Plan`
- `plan` prints or persists it
- `run-process` walks the resolved plan as a DAG (the primary execution path)
- `run-step` executes one resolved step from it (plumbing)
- `run-parallel` executes one resolved fan-out step from it (plumbing)

The user-facing execution model is two commands:

- **`run-process`** -- execute a full process DAG (all steps in dependency order)
- **`run-step`** -- execute a single step (for debugging or manual orchestration)

`run-parallel` is internal plumbing used by `run-process` for fan-out steps and by
worker VM entrypoints.
Cloud worker-VM fan-out is triggered via `run-process --backend gcp-worker`; there is no
standalone cloud-dispatch command.

## 8.2 Example Resolved Plan

The resolved plan uses the `plan:` envelope convention with a `schema` token.

```yaml
plan:
  schema: metaproc:Plan/0.6
  generated_at: '2026-04-17T17:54:42'
  process: example_plugin/process/mine/mine.process.md
  params:
    RUN_ID: doc-sync-demo
    DATASET: tech-mix-5
    RUNS_DIR: runs/local/example-workflow
    dataset: tech-mix-5
  deps:
    roster:
      path: runs/local/example-workflow/doc-sync-demo/mine/progress.md
      produced_by: setup-roster.roster
      consumers: [create-mine-overview, create-mine-summary, generate-record, review-batch]
  steps:
    - id: setup-roster
      mode: code
      handler: setup_roster.py:setup_roster
      outputs:
        roster:
          path: runs/local/example-workflow/doc-sync-demo/mine/progress.md
          kind: file
          format: frontmatter-md
          optional: false
    - id: generate-record
      mode: agent
      fan_out:
        over: deps.roster
        bind: event_id
        source: runs/local/example-workflow/doc-sync-demo/mine/progress.md
        bind_fields: [event_id, item, period, event_date, category]
        batch_size: 20
        items: []
        filtered_count: 0
      needs: [setup-roster]
```

## 8.3 Command Semantics

Implemented CLI surface:

**Primary user commands:**

- `run-process` -- execute a process spec DAG: all steps in dependency order, with
  parallel execution of independent steps.
  Supports `--backend` (local, gcp-worker), `--cloud` (run orchestrator on GCP),
  `--only`/`--from`/`--skip`/`--force`/`--continue-on-error`, `--dry-run`. See section
  19 for full details.
- `plan` -- resolve and display execution plan; `--format {yaml,svg,html}` emits a
  resolved plan YAML or renders the process as a static visualization.
  See the public
  [MetaBrowser architecture](https://github.com/jlevy/metabrowser/blob/main/docs/architecture.md)
  for the browser visualization plane.
- `deps` -- show declared deps with inferred runtime state for the planned run
- `validate` -- validate declared outputs
- `check-headers` -- walk the process tree and validate frontmatter
- `status` -- run-level progress report: per-variant item counts, timing, system
  metrics; `--check` mode for agent orchestration (exit code indicates completion
  state); `--format json` for structured output
- `wait` -- block until a run reaches terminal state (all items completed or failed);
  designed for multi-phase agent orchestration, replacing ad-hoc polling loops
- `kill` -- terminate a running pool and its child processes via sentinel protocol, or
  terminate the local orchestrator lease owner before the first pool exists; supports
  `--drain` (stop new launches), `--force` (SIGKILL), `--signal`, `--variant`
- `tail` -- tail/view JSONL event logs (auto-detects adapter format)
- `compare` -- compare two item directories side-by-side
- `compare-matrix` -- cross-variant comparison matrix for a run
- `stats` -- usage and cost analysis
- `write-usage` -- aggregate log files and produce usage.md cost report
- `resource-report` -- build or refresh a hierarchical `resources.json` run report
- `override` -- run-scoped operator escape hatch for dependency gates; writes
  `overrides.yaml` under `.state/`
- `variants` -- list execution profiles (adapter/model combinations) available to a
  process
- `trace` -- unified workflow event-trace CLI; extracts, links, and writes `trace.jsonl`
  under `.logs/derived/`

**Plumbing commands:**

- `run-step` -- execute a single non-fan-out step
- `run-parallel` -- execute a fan-out step across items (used internally by
  `run-process` and worker VM entrypoints)

**Utility commands:**

- `auth-check` -- verify adapter credentials
- `claude-auth push|show|rotate` -- manage the Claude Code CLI Personal-Plan OAuth
  credential in Secret Manager for Batch workers (see §21.14)
- `codex-auth push|show|rotate` -- manage the Codex CLI ChatGPT-OAuth credential in
  Secret Manager for Batch workers
- `compact-logs` -- compact JSONL event logs (single file or recursive directory)
- `check-handlers` -- resolve every `mode: code` handler in a process tree (verifies
  file paths and callable references)
- `env` -- inspect the `MetaprocEnv` registry: lists every registered env var with kind,
  current value, and description
- `softschema` -- softschema inspection, validation, and compilation
- `resources` -- print the calling process’s resource context (CPU, RAM, cgroups, env)
- `liveness-watch` -- stall backstop supervisor for long-running dispatches
- `resume-daemon` -- non-LLM checkpoint-driven re-dispatch loop for retry-later items
- `run-manifest` -- validate and normalize run manifest YAML
- `gzip-text` -- idempotently gzip text-format files to reclaim disk

**GCP subcommands (`metaproc gcp ...`):**

- `gcp status <target>` -- show Batch job status (auto-detect: local run dir or run-id)
- `gcp scale <target> --step <step>` -- update desired topology for an active cloud
  fan-out step
- `gcp logs <target>` -- stream Cloud Logging entries (auto-detect: local run dir or
  run-id)
- `gcp cancel <target>` -- cancel running/queued Batch jobs (auto-detect: local run dir
  or run-id)
- `gcp runs` -- list all active metaproc runs across the project
- `gcp self-install` -- install metaproc on a remote GCP VM via SSH
- `gcp resources` -- show metaproc-related GCP assets via Cloud Asset Inventory
- `gcp filestore` -- inspect Filestore instance status and utilization
- `gcp archive` -- sync run directories to GCS for long-term retention
- `gcp remote` -- run metaproc commands on the gateway host via SSH/IAP
- `gcp remote-run` -- launch `run-process` in a remote tmux session
- `gcp cleanup` -- delete old terminal-state Batch jobs

**Pool subcommands (`metaproc pool ...`):**

- `pool status` -- show RunPool live status (concurrency, pressure, active processes)
- `pool events` -- show RunPool event log (starts, exits, kills, pressure checks)
- `pool retry-missing` -- reset completion markers for items with missing cloud outputs

The `qa` surface is intentionally not a standalone framework command: QA remains
process-owned and is typically expressed as ordinary steps inside the DAG.

## 9. Runtime Model

The authored model stays small.
The emitted runtime model is explicit.

The four artifact groups (run-level state, per-step state, per-task state, logs)
populate the `.state/` and `.logs/` branches at every scope root.
For the per-file reference (filename, format, schema, lifecycle, writer, and readers),
see [artifact-catalog.md](../artifact-catalog.md).
Sections 9.2-9.6 below cover the engine’s contract on the load-bearing files
(`status.yaml`, `attempt.yaml`, `result.yaml`, `.logs/*.jsonl`, `process-events.jsonl`)
in depth.

## 9.1 Example Runtime Layout

```text
runs/local/example-workflow/doc-sync-demo/
  .state/
    run-config.yaml
    process-status.yaml
    orchestrator-lease.yaml
    steps/
      generate-record/
        runpool-status.yaml
        scale-state.yaml
    tasks/
      setup-roster/
        status.yaml
        result.yaml
      generate-record/
        AAPL-2025Q2/
          status.yaml
          attempt.yaml
          result.yaml
        MSFT-2025Q2/
          status.yaml
          attempt.yaml
          result.yaml
  .logs/
    process-events.jsonl
    runpool/steps/generate-record/events.jsonl
    tasks/generate-record/AAPL-2025Q2/generate-record_AAPL_2026-04-17T17-54-42.jsonl
    derived/trace.jsonl
  mine/pi-deepseek-v3.2/
    AAPL-2025Q2/
      record.md
```

The harness contract is the `.state/` and `.logs/` branches at the run-dir root.
The artifact tree (here, `mine/pi-deepseek-v3.2/<item>/`) is whatever the spec’s output
templates produce; the engine writes no bookkeeping into it.
Per-task state is keyed by the explicit `for_each.key` template, not inferred from the
output path, so two non-fan-out steps that share an output parent dir keep separate
state. Here, task is a runtime term: one execution record for a step applied to an item.
It is not a synonym for the item itself.

## 9.2 `status.yaml`

`status.yaml` is the harness-owned task state record.

> **Implementation note:** Runtime artifacts (`status.yaml`, `attempt.yaml`,
> `result.yaml`) use plain YAML, not envelope-wrapped frontmatter.
> They are machine-internal records, not authored documents -- the envelope convention
> (section 6.1) applies only to the authored surface.

```yaml
run_id: 2026-03-24-daily
step_id: predict-item
item:
  item: AAPL
  category: technology
state: completed
attempt: 2
started_at: "2026-03-24T08:35:22Z"
completed_at: "2026-03-24T08:48:11Z"
last_heartbeat_at: "2026-03-24T08:47:58Z"
error: null
```

Allowed states:

- `pending`
- `running`
- `completed`
- `failed`
- `cached`

Only the harness writes this file, and it must write it atomically.

## 9.3 `attempt.yaml`

`attempt.yaml` records what was actually launched.

```yaml
run_id: 2026-03-24-daily
step_id: predict-item
item:
  item: AAPL
  category: technology
params:
  DATE: "2026-03-24"
  REFERENCE_DATE: "2026-03-21"
  RUN_ID: 2026-03-24-daily
  item: AAPL
inputs:
  items: example_plugin/runs/2026-03-24-daily/predict/items.md
outputs:
  prediction: example_plugin/runs/2026-03-24-daily/predict/claude-cli/AAPL/prediction.md
runtime:
  adapter_type: claude-code-cli
  model: opus
  timeout_s: 1200
```

This fills the gap between raw JSONL logs and human-facing items files.

## 9.4 `result.yaml`

`result.yaml` records the final validated outcome.

```yaml
run_id: 2026-03-24-daily
step_id: predict-item
state: completed
validated: true
outputs:
  prediction: example_plugin/runs/2026-03-24-daily/predict/claude-cli/AAPL/prediction.md
published_at: "2026-03-24T08:48:11Z"
```

## 9.5 `.logs/*.jsonl`

The low-level streaming record now lives under the run-scoped `.logs/` directory rather
than as a per-item `.state/events.jsonl` file.
Framework-owned JSONL logs include adapter/session logs, `process-events.jsonl`, and
runpool `events.jsonl` streams under `.logs/runpool/`. Workflow-owned tool streams live
under `.logs/tools/<tool-name>/`. Derived outputs such as trace JSONL live under
`.logs/derived/`. For the command map and current paths, see
[metaproc-operator-reference.md](../../src/metaproc/docs/metaproc-operator-reference.md).

It is useful for:

- live monitoring
- debugging
- adapter inspection
- usage/cost extraction

It is not the only machine-readable runtime artifact.

### Supporting tool: `metaproc tail`

The framework ships a first-class `metaproc tail` command for formatted viewing of
`.logs/*.jsonl` files across adapters.

Key capabilities:

- **Auto-detection**: sniffs the first JSON line per file to select the correct parser
  (Claude Code vs Gemini CLI vs Pi CLI), so mixed-adapter `.logs/` directories work
  transparently.
- **Delta coalescing**: Gemini CLI emits many small `delta:true` streaming fragments;
  the parser accumulates these into single readable text events.
- **Noise filtering**: non-JSON lines in Gemini output (stderr interleaved into JSONL)
  are suppressed unless they contain error signals.
- **Agent-friendly**: colors and the status line auto-disable when output is piped
  (non-TTY or `NO_COLOR`). The `--once` flag reads all content and exits without
  requiring Ctrl-C, making it usable from other coding agents.
- **Summary table**: `--summary` prints per-file adapter, status, duration, and cost on
  exit.

The parsing library (`metaproc.logutil.parsing`) is import-friendly with no CLI
dependencies, so other tools can reuse the parsers and event model.

### Monitoring surface summary

The framework provides six complementary monitoring layers:

| Layer | Command | Data source | Purpose |
| --- | --- | --- | --- |
| Log-level | `tail` | `.logs/**/*.jsonl` | Real-time streaming events, per-file status, cost |
| Item-level | `status` | `.state/tasks/.../status.yaml` plus `process-status.yaml` | Run progress counts, timing, completion checks |
| Process-level | `pool status` | `runpool-status.yaml` | Live process health (RSS, descendants, kills) |
| DAG-level | `tail` | `process-events.jsonl` | Process orchestrator events (step lifecycle, levels) |
| Cloud-level | `gcp status <run-id>` | GCP Batch API | Orchestrator and worker states by exact run key, with a legacy-label fallback |
| Visual | `metab` (external MetaBrowser CLI) | source logs, runpool events, process events, derived trace | Browser-based charts and log exploration |

`tail` answers “what is this agent doing right now?”
`status` answers “how is the run going overall?”
`pool status` answers “how are the processes performing?”
`tail <process-events.jsonl>` answers “what is the DAG orchestrator doing?”
`gcp status <run-id>` answers “what are the cloud jobs doing?”
`metab` answers “what does the full run look like over time?”

For agent orchestration, `status --check` and `wait` replace ad-hoc shell monitoring.
See section 18 for details.
For run pool design details, see [arch-runpool.md](arch-runpool.md).

### Supporting Tool: MetaBrowser (`metab`)

The external `metab` CLI (from the standalone MetaBrowser package) launches a local web
server for browsing run artifacts, logs, and results.
The complete architecture, including the file-kind registry, view registry, charts,
visualization plane, and remote tunnel, lives in
[MetaBrowser architecture](https://github.com/jlevy/metabrowser/blob/main/docs/architecture.md).

The browser classifies files into a **file kind** taxonomy (`agent-log`, `runpool-log`,
`process-log`, `markdown`, `text`, etc.)
and offers kind-appropriate **view tabs** (Charts, Log, Raw JSON, Rendered, Source) via
a data-driven view registry.

The `process-log` kind detects `process-events.jsonl` files (DAG orchestrator events)
via `ProcessLogParser` in `logutil/parsing.py` and offers Log and Raw JSON views.
The `logutil/parsing.py` module provides six adapter-specific parsers
(`ClaudeLogParser`, `CodexLogParser`, `GeminiLogParser`, `PiLogParser`,
`RunPoolLogParser`, `ProcessLogParser`) with auto-detection via `detect_adapter()`.

The **Charts view** uses Chart.js (CDN-loaded) to render time-series operational
dashboards:

- **RunPool events** (`.logs/runpool/**/events.jsonl`): memory pressure area chart,
  running process count over time, concurrency cap line, kill/adjust annotations.
- **Agent logs** (Claude/Gemini/Pi `.jsonl`): event activity over time as stacked bars
  (tool calls, text, thinking, errors).

Each chart tab includes a collapsible taxonomy-path tally tree summarizing event counts.
Charts auto-refresh while the underlying file is actively being written to.

## 9.6 `process-events.jsonl`

`process-events.jsonl` is a DAG-level runtime artifact written by the
`ProcessEventLogger` during `run-process` execution.
It complements the adapter/session logs described in section 9.5 by recording
process-wide lifecycle events rather than per-agent streaming events.

Written to `{run_dir}/.logs/process-events.jsonl`. Code step stdout/stderr is captured
under task execution logs: `{run_dir}/.logs/tasks/{step_id}/process_<ts>.log` for scalar
steps and `{run_dir}/.logs/tasks/{step_id}/<item_key>/process_<ts>.log` for item-scoped
work.

Event types (13 total):

| Event | Fields | When |
| --- | --- | --- |
| `process_start` | process, run_id, backend, step_count | DAG execution begins |
| `process_complete` | process, run_id, completed, failed, skipped, elapsed_s | DAG execution ends |
| `level_start` | level, steps | topological level begins |
| `level_complete` | level, elapsed_s | topological level ends |
| `step_start` | step_id, mode | step execution begins |
| `step_complete` | step_id, elapsed_s | step succeeds |
| `step_fail` | step_id, elapsed_s, error | step fails |
| `step_skip` | step_id, reason | step skipped (user skip or previously completed) |
| `step_blocked` | step_id | step blocked due to upstream failure |
| `worker_dispatch` | step_id, num_workers, items_count | fan-out dispatched to workers |
| `item_start` | step_id, item_key | fan-out item begins |
| `item_complete` | step_id, item_key | fan-out item succeeds |
| `item_fail` | step_id, item_key, error, failure_class | fan-out item fails |

All events include an auto-injected `ts` timestamp.
The format is compatible with RunPool events for unified browser display.

## 10. Resumability and Publication Semantics

## 10.1 Harness-Owned Publication

Completion is published by the harness, not inferred from partial output presence.

The rule is:

1. the agent writes its declared outputs
2. the harness validates them
3. the harness atomically writes `result.yaml`
4. the harness atomically transitions `status.yaml` to `completed`

Future hardening can add:

- temp path output staging
- explicit publish moves into final destinations

## 10.2 Reuse Policy

The design uses `reuse_policy` rather than a bare `cache` field.

Values:

- `validated_outputs`
- `exact_inputs`
- `never`

Semantics:

- `validated_outputs` for most agent and manual steps
- `exact_inputs` for deterministic code steps
- `never` for always-run steps

## 10.3 Reuse Key Inputs

For agent steps, the reuse decision can eventually consider:

- process spec digest
- instruction digests
- parameter values
- input artifact digests
- declared reuse policy

This provides a path from simple existence-based reuse toward fingerprint-based reuse.

The fingerprint is a first-class invalidation signal on resume.
The orchestrator stores the step’s current fingerprint at completion time (mirrored at
`.state/process-status.yaml` under `steps.<step_id>.recorded_step_hash`, with a fallback
to per-task `result.yaml`) and consults it inside `_is_step_completed` on the next run.
A mismatch demotes the completed verdict back to “needs rerun” and cascades downstream
via `_invalidate_downstream`, so editing a step’s `prompt_paths` runbook (or composite
`uses_path`) and rerunning the same `RUN_ID` re-executes only the changed step plus its
descendants. Runs whose completion records carry no `recorded_step_hash` are treated as
legacy completions and are not re-executed on resume.

## 10.4 Recovery Rules

Recovery semantics are explicit:

- `completed` with validated outputs and a matching fingerprint -> skip
- `completed` with a fingerprint mismatch -> rerun this step and downstream
- `failed` -> retry (with retry policy if configured)
- `cached` -> skip
- `running` with live process -> do not reclaim
- `running` with dead process or stale heartbeat -> reclaim
- outputs exist but status is missing or not `completed` -> treat as incomplete and
  revalidate or rerun

The normal resume path does not require humans to manually clean partial outputs.

## 10.5 Resume-Safe Discovery

The discovery engine (`discover_items_from_source`) provides resume-safe item filtering.
For each item in the fan-out source file:

1. Compute the item key from the step’s `for_each.key` template.
2. Read `.state/tasks/{step_id}/{item_key}/status.yaml`.
3. Filter out items with terminal status (`completed`, `cached`, or domain-registered
   terminal statuses).
4. Re-add `failed` or stale `running` items as actionable.
5. Also filter items whose `status` field in the source file itself is terminal.

The result is a `FanOutDiscovery` with `actionable_contexts` (items to process) and
`filtered_items` (items skipped with reason).
This makes resume a normal code path, not a special recovery mode.

## 11. Fan-Out and Items Files

An **items file** is a list-typed dep that drives a fan-out step.
Its parsed value is `list<map_item>`, a list of records (YAML maps), each with named
fields, where each record drives one iteration of the step.
The role is declared by a step’s `for_each.over: deps.<name>` reference, not by a
spec-level `role:` tag (the `DepRole` enum was retired in the 2026-04-23 simplification;
see “Historical note: packets” below).
The framework parses items files generically by extracting the envelope payload’s
`items` list. Domain packages supply typed envelope models; the orchestration layer only
needs the generic items-file contract.

**Terminology note: items file vs roster.** *Items file* is the framework’s primary term
for this concept. *Roster* is retained as domain-specific language inside the
illustrative `example_plugin` profile, where step IDs, dependency names, and module
names like `setup-roster` and `mine/roster.py` use the older word.
It is not part of the framework’s vocabulary.
The two terms refer to the same construct.

**Fan-out and map.** Two framings of the same operation: *fan-out* names the operational
shape (one step, many parallel workers, dispatch via run pool or worker dispatch), and
is the term used throughout the framework code.
*Map* names the functional operation (`map(step, items)`, applying the step to each
element of an items file).
They describe the same thing from different angles, not two different things.
Code keeps `fan-out`; design conversations may use either.

## 11.1 Candidate Source, Not Authoritative Completion State

For the illustrative downstream profile:

- `items.md` or `events.md` is the shared candidate items file at process scope
- per-item `.state/tasks/{step_id}/{item_key}/status.yaml` is the authoritative
  completion record
- the harness joins those two surfaces to compute actionable work

This avoids concurrent writers mutating one shared frontmatter file and keeps the same
items file reusable across variants.

## 11.2 Example Items File (`items.md`)

```yaml
---
items:
  schema: "example_plugin:ItemsDocument/0.1"
  event_date: "2026-03-24"
  items:
    - item: AAPL
      category: technology
      report_session: after_close
    - item: MSFT
      category: technology
      report_session: after_close
---
# Items

Shared items file for all variants. Per-item completion state lives in
`.state/tasks/{step_id}/{item_key}/status.yaml`.
(Analysis-domain code calls this a *roster*; the framework calls it an *items file*.)
```

The framework does not need to know what a item is.
It only needs a declared items file whose parsed value is `list<map<...>>`.

## 11.3 Example Events File (`events.md`)

```yaml
---
events:
  schema: "example_plugin:MineRosterDocument/0.1"
  source_dataset: tech-mix-500
  items:
    - event_id: AAPL_2025-Q1
      item: AAPL
      period: 2025-Q1
      event_date: "2025-01-30"
      category: technology
---
# Mine Events

Fan-out source for mine. Authoritative completion state still lives in per-task
`.state/tasks/{step_id}/{item_key}/status.yaml`.
```

The exact envelope name is domain-owned.
From the framework’s perspective this is just an items-file dep produced by
`setup-roster` (an analysis step ID; the framework would name a similar step
`setup-items`).

## 11.4 Fan-Out Lifecycle

`run-process` is the production entry point.
It walks the full DAG, dispatches composite children recursively, and delegates fan-out
steps to the configured local or cloud backend.
`run-parallel` remains plumbing for worker VMs and low-level troubleshooting.

```text
run-process (DAG orchestrator)
  -> resolve deps and build plan
  -> group steps into dependency levels
  -> execute each runnable level
  -> recurse into child processes for composite steps
  -> dispatch fan-out items to local pools or cloud workers
  -> validate outputs and write runtime state

items-producing step
  -> materializes the shared items-file dep at process scope

fan-out runtime
  -> joins items-file entries with per-item `.state/` records
  -> launches only actionable items
  -> validates outputs and writes per-item state
```

## 11.5 Optional Preview Artifacts

Preview artifacts such as `{step-id}.items.yaml` are derived from the source and status
store. They are informational only, not authoritative execution state.

## 11.6 Historical Note: Packets

Between mid-2025 and 2026-04-23, structured multi-form agent steps (predict, retro)
declared their output contract through a **packet manifest**, a `v<N>/packet.yaml` file
alongside the templates.
A `ProcessDep` with `role: packet` pointed the engine at the manifest; `PacketForm`
entries carried `template:`, `output:`, `frontmatter_key:`, and optional `condition:`
fields, and the engine’s `_resolve_packet_outputs` helper expanded the manifest into
per-item expected output filenames at validate time.
A sibling `detect_latest_packet_version` helper and a `default_strategy: latest-packet`
on `ProcessInput` auto-picked the newest version directory when the operator omitted
`FORM_VERSION`.

The packet layer was retired in one PR on 2026-04-23. Every piece of information it
carried was already primary elsewhere:

| What the packet layer carried | Where it lives now |
| --- | --- |
| Ordered list of expected outputs for a step | Inline `step.outputs` declarations (dict insertion order) |
| Template pointer per output | `step.outputs.<name>.template:` field |
| Conditional output (e.g. leakage-check under backtest) | `step.outputs.<name>.condition:` field, evaluated via `metaproc.engine.condition.output_is_active` |
| Per-form schema reference | Already on the output’s own frontmatter; existing schema registry resolves it |
| “Latest on-disk version” default | Explicit `default: "vN"` on the `form_version` / `retro_version` `ProcessInput`; set the literal and override via CLI/env |
| Versioning convention (`process/<name>/vN/…`) | Unchanged on disk; `{{form_version}}` template variable still drives the path |

The `DepRole` enum
(`template | packet | process | roster | run-input | run-output | kb-index`) retired
with the packet layer; all seven values were either redundant with other declarations
(composite `uses:` for `process`, `for_each.over:` for `roster`, `{{run.dir}}` prefix
for `run-input`/`run-output`) or engine-opaque documentation tags.
The viz side panel’s `role:` chip is replaced with a `usage:` list derived from step
references (verbatim `uses` / `for_each.over` / `with` / `prompt_paths` / `inputs`).

Legacy on-disk `packet.yaml` files (e.g. `predict/v10/packet.yaml`) are retained as
historical artifacts; no loader code reads them.

## 12. Adapter Contract

Reference implementation details such as exact CLI flags belong in adapter-specific
documentation; the contract below is adapter-neutral.

## 12.1 Adapter Contract

```yaml
adapter:
  type: claude-code-cli
  config:
    model: opus
    timeout_s: 1200
    budget_usd: 5.0
    tools: [Read, Write, Edit, Bash, Grep, Glob, WebSearch, WebFetch]
```

The adapter contract describes:

- input artifacts
- output artifacts
- instructions
- working directory
- runtime config
- optional workspace config
- event stream (including `tool_execution_start` / `tool_execution_end` and
  `rate_limit_event` records consumed by §14.7 Tool-use Observability)
- final adapter metadata

## 12.2 Reference Adapters

Registered adapters (in `ADAPTER_REGISTRY`):

- `claude-code-cli` -- invokes `claude -p @<prompt>`. Supports model, effort,
  permission-mode, tools, max-budget, output-format, verbose, worktree,
  strict-mcp-config flags.
  Three auth modes, resolved in this order:
  1. `ANTHROPIC_API_KEY` (pay-per-token; used on laptops and CI).
  2. Interactive login session at `~/.claude/` (personal-plan subscription; used on dev
     laptops after `claude login`).
  3. Personal-plan OAuth blob via Secret Manager on GCP Batch workers: the operator
     pushes the Keychain-held `~/.claude/.credentials.json` to Secret Manager with
     `metaproc claude-auth push`; dispatch binds `METAPROC_GCP_SECRET_CLAUDE_CREDS` →
     `CLAUDE_CODE_CREDS_JSON` as a Batch `secret_variables` entry (see §21.14); the
     adapter’s `bootstrap(home)` hook materializes the credential file on first use (see
     §21.2).
- `codex-cli` -- invokes
  `codex <top-level-flags> exec --json <exec-flags> <prompt_text>` (reads prompt file
  inline; no `@<file>` surrogate, mirrors gemini’s pattern).
  Supports model (gpt-5.x and o-series via `CODEX_VALID_MODELS`), effort (via
  `-c model_reasoning_effort=`), sandbox (read-only / workspace-write /
  danger-full-access), approval_policy (untrusted / on-failure / on-request / never),
  permission_mode (bypassPermissions maps to
  `--dangerously-bypass-approvals-and-sandbox`; the other three map to explicit sandbox
  \+ approval pairs; the adapter never emits `--full-auto`), tools (WebSearch/WebFetch →
  `--search`; Bash/Read/Write/Edit/Grep/Glob satisfied by sandbox; unknown tools
  log.debug-dropped), append_system_prompt (inlined above the prompt body),
  working_directory (`-C <dir>` in exec-flag group), and config_overrides (raw
  `-c key=value` passthrough with JSON-encoded values).
  Grammar-aware flag placement is load-bearing: `-a/--ask-for-approval` and `--search`
  are top-level-only and must precede `exec` or codex rejects them with
  `unexpected argument`. Two auth modes, resolved in this order:
  1. `OPENAI_API_KEY` (Vehicle A; pay-per-token; primary path for laptops, CI, and GCP
     Batch workers).
  2. ChatGPT-plan OAuth blob at `~/.codex/auth.json` with `tokens.auth_mode=chatgpt`
     (Vehicle B; subscription-billed, free-per-request).
     Headless push requires `cli_auth_credentials_store = "file"` on the seeding laptop
     (see `research-2026-04-23-codex-cli-auth.md` §F10).
  3. GCP Batch Vehicle B via Secret Manager: `metaproc codex-auth push` pushes
     `~/.codex/auth.json` to Secret Manager; dispatch binds
     `METAPROC_GCP_SECRET_CODEX_CREDS` → `CODEX_CREDS_JSON` as a Batch
     `secret_variables` entry; the adapter’s `bootstrap(home)` materializes
     `{home}/.codex/auth.json` (mode 0600, parent 0700), pops the env var to prevent
     child-process leaks, and rejects `apikey` blobs (API-key auth should arrive as
     `OPENAI_API_KEY` directly).
     Event parsing uses codex-cli 0.124.0’s flat top-level `type` envelope: terminal
     events are `turn.completed` (success; carries
     `usage.{input_tokens, cached_input_tokens, output_tokens}` inline) and
     `turn.failed` (failure; `error.message`). Intermediate `error` events are
     codex-internal reconnect retries and not terminal.
     Log compaction strips `item.started` / `item.updated` / intermediate `error`; keeps
     `item.completed` (agent_message / reasoning / command_execution / file_change /
     mcp_tool_call / web_search / todo_list) and both terminal events.
- `gemini-cli` -- invokes `gemini -p <prompt_text>` (reads prompt file inline).
  Supports model, permission-mode (yolo), output-format, sandbox.
  Injects system prompt and native settings (thinking config, agent overrides) via temp
  files and env vars. Auth via `GEMINI_API_KEY`, Vertex AI, or OAuth.
- `pi-cli` -- invokes `pi -p @<prompt> --no-session`. Supports `--provider` (anthropic,
  openai, google, vertex, azure, bedrock, plus custom providers), `--model`
  (cross-provider roster), `--thinking`, `--tools`, `--verbose`. Auth via
  `~/.pi/agent/auth.json` (from `pi /login`), provider-specific env vars, custom
  provider config in `~/.pi/agent/models.json` (supports shell commands for dynamic
  token minting), or injected `api_key` from the harness (for vertex-maas per-batch
  token injection). Event parsing extracts usage from `agent_end` events; log compaction
  strips streaming noise while preserving thinking chains.

Authoritative step success must still be defined by:

- declared artifacts
- emitted runtime records
- harness validation

Not by conversational state in a parent agent.

### Pi CLI adapter: multi-provider model dispatch

The Pi adapter enables running the same process spec against models from multiple
providers without changing process definitions.
The adapter maps the framework’s `provider` and `model` config fields to Pi’s
`--provider` and `--model` CLI flags.

Valid models span built-in provider families and custom providers:

- **Anthropic**: sonnet, opus, haiku (and full model IDs)
- **OpenAI**: gpt-4.1, o3, o4-mini
- **Google**: gemini-3.1-pro-preview, gemini-3-flash-preview,
  gemini-3.1-flash-lite-preview
- **Vertex AI MaaS** (custom provider): zai-org/glm-5-maas,
  moonshotai/kimi-k2-thinking-maas

Built-in providers: anthropic (default), openai, google, vertex, azure, bedrock.
Custom providers (defined in `~/.pi/agent/models.json`): any OpenAI-compatible endpoint.

#### Vertex AI MaaS custom provider

**Design rationale (authoritative mechanism in
[arch-cloud-execution.md §3.12](arch-cloud-execution.md) and
[arch-authentication.md](arch-authentication.md), UC-5 / UC-6):** third-party models in
Vertex AI Model Garden (GLM-5, Kimi K2) expose an OpenAI-compatible endpoint that Pi
connects to via a custom `vertex-maas` provider in `~/.pi/agent/models.json`. The
`apiKey` field is a placeholder; Metaproc injects a fresh `google.auth` access token per
batch (**not per item**) and Pi sends it as `Authorization: Bearer <token>`. This is why
the framework treats `vertex`-prefixed providers specially in the adapter: it resolves
the token once via `resolve_gcp_token()` and writes it into each item’s
`runtime_config`, instead of leaving the operator to refresh tokens manually.
Failure raises an exception; there is no degraded fallback path.

This indirection is what makes the multi-model backtest work without per-model operator
setup: the same process spec runs with different adapter config overrides at invocation
time.

## 13. QA and Validation

QA is a **domain concern**, not a framework feature.
Metaproc provides the execution substrate, including handler invocation, `.state/`
recording, and dependency ordering, but has no opinion on check taxonomies, severity
models, or report formats.

Domains implement QA as ordinary `mode: code` step handlers.
A downstream package owns its QA end-to-end: contracts, check rules, reporting, and any
CLI handler. For example, a package can expose `example_plugin.qa.handler:check` and
reference it from a `qa-check` code step.

This follows the three-layer model:

1. **Metaproc** (layer 1): orchestration, contracts, state, resumability
2. **Shared process libraries** (layer 2): reusable validation patterns, extracted when
   a second domain proves the need
3. **Domain code** (layer 3): specific checks, report formats, severity rules

Check taxonomies, severity models, and report formats stay in the domain layer rather
than the framework.

Failure handling splits along the same seam, and the dividing line is worth stating
because both halves are easy to put in the wrong layer:

> **The framework owns what a failure does to execution.
> The domain owns what a failure means.**

Retrying, failing a step, and aborting a run are framework business because only the
framework can perform them.
Severity, ownership, and taxonomy are the domain’s, and the framework should be unable
to read them even when it stores them on the domain’s behalf.

### 13.1 Plugin System

The plugin system separates generic framework concerns from domain-specific logic.
Domains register their types and conventions through a protocol-based registry.

### 13.1.1 Plugin Protocol

```python
class MetaprocPlugin(Protocol):
    name: str
    def register(self, registry: PluginRegistry) -> None: ...
```

The registry supports registering:

- **Schemas** -- Pydantic models by schema token
- **Envelopes** -- Pydantic models by document type (e.g., `prediction:`, `items:`)
- **Schema-to-envelope mappings** -- which envelope wraps which schema
- **Terminal statuses** -- for progress tracking (e.g., `completed`, `cached`)
- **Process rules** -- validation rules for process specs
- **Compare defaults** -- fields and envelope keys for cross-variant comparison

The registry does not carry process-embedded form metadata hooks.
Packet manifests, template directories, and other domain dependencies are ordinary
declared files, not plugin-side conventions.

### 13.1.2 Discovery

Two-phase plugin discovery:

1. **Standard entry points**:
   `importlib.metadata.entry_points(group="metaproc.plugins")` for installed packages
2. **Workspace fallback**: walks up from CWD to find the workspace root
   (`pyproject.toml` with `[tool.uv.workspace]`), scans all workspace members for
   `metaproc.plugins` entry points, loads them via `importlib.import_module`

The workspace fallback ensures plugins work when running `uv run --project metaproc`
without `--all-packages`.

### 13.1.3 Example Plugin Registration

The fictitious downstream plugin (`example_plugin.metaproc_plugin`) registers:

- Envelope types from its document model registry
- Schema models from its schema registry
- Schema-to-envelope mappings for its document set
- Terminal progress statuses
- Compare-matrix defaults (direction, move_pct, position_type, allocation)

Entry point declaration in `pyproject.toml`:

```toml
[project.entry-points."metaproc.plugins"]
example = "example_plugin.metaproc_plugin:plugin"
```

## 14. Robustness Subsystems

These subsystems were added to handle the realities of running hundreds of agent
subprocesses against external APIs.
They are all harness-owned -- agents never interact with them directly.

### 14.1 Retry System

The retry system classifies subprocess failures as **retryable (transient)** or
**permanent** and automatically retries transient failures with exponential backoff.

#### Error Classification (Retry Verdict)

The subprocess is treated as opaque (exit code is always 1 for failures), so the error
string from `status.yaml` is the only signal.
`classify_error()` determines retryability (`RetryVerdict`: `RETRY` or `FAIL`) via a
priority chain:

1. **Permanent patterns** (checked first): `enospc`, `enomem`, `quota`,
   `permission denied`, `billing`, `credits`, exit code 137/143, `cancelled` -- always
   `FAIL`.
2. **Transient patterns**: `timeout`, `rate limit`, `429`, `truncated_headers`,
   `gcloud auth`, `unavailable`, `503`/`502`, `econnrefused`/`econnreset`, `connection`,
   `log_runaway` -- produce `RETRY`.
3. **Bare “exit code N”** -- default to `RETRY` (most are transient API errors).
4. **”output validation failed”** -- classified as `RETRY` unless
   schema/envelope/mismatch (then `FAIL`).

The intent of rule 4 is that a missing output is transient, because the agent may have
been killed before writing it, while a structural mismatch is permanent.
The implementation cannot honour that intent, because the structured facts that separate
the two cases are flattened into the error string before `classify_error` sees it, and
the substring test then reads the artifact’s filename along with everything else.
Two declared outputs of one process, each missing for the same transient reason:

```text
output validation failed: company-research-schema-manifest.md: file not found   -> FAIL
output validation failed: source-snapshot.md: file not found                    -> RETRY
```

Identical failures, opposite verdicts, because one filename contains `schema`.
[plan-2026-08-20-contract-failure-primitives.md](../project/specs/active/plan-2026-08-20-contract-failure-primitives.md)
proposes re-expressing the rule over the structured failure record.
5. **Default** -- `FAIL`.

#### Failure Classification (Failure Reason)

Separately from the retry verdict, `classify_failure()` categorizes the *reason* for
failure into a `FailureClass` enum for observability and aggregation:

| FailureClass | Patterns |
| --- | --- |
| `RATE_LIMITED` | rate limit, 429, quota |
| `TIMEOUT` | timeout, stalled, log_runaway |
| `SERVER_ERROR` | 503, 502, connection errors |
| `INVALID_OUTPUT` | output validation failed |
| `CRASH` | bare exit codes |
| `UNKNOWN` | default |

Failure classes are recorded per-item and aggregated into `FailureCounts` in the
`RunPoolStatus` model.
`FailureCounts` tracks `rate_limited`, `server_error`, `timeout`, `invalid_output`,
`crash`, and `unknown` counts -- one counter per `FailureClass` value.
This surfaces in `pool status` and in NFS error detail extraction for cloud workers.

#### Retry Policy

`RetryPolicy` is a Pydantic model with four fields:

| Field | Default | Purpose |
| --- | --- | --- |
| `max_retries` | 0 (off) | Maximum retry attempts per item |
| `initial_backoff_s` | 5.0 | First retry delay |
| `backoff_multiplier` | 2.0 | Exponential multiplier |
| `max_backoff_s` | 120.0 | Backoff cap |

Backoff: `initial_backoff_s * (backoff_multiplier ^ (attempt - 1))`, capped at
`max_backoff_s`.

#### Policy Resolution

The retry policy resolves through a priority chain:

1. `--no-retry` CLI flag (disables retry entirely)
2. `--max-retries` CLI override
3. step-level `for_each.retry`
4. process-level `defaults.retry`
5. `RetryPolicy()` (off by default)

#### Log Error Extraction

`extract_log_error` reads the last 30 lines of a subprocess JSONL log, looking for
structured `agent_end` events with `errorMessage`, then for raw non-JSON stderr lines.
This enriches the status error string, which feeds back into `classify_error`.

#### Integration

The retry scheduler is orchestrator-owned in `run_parallel._run_agent_pool`. Three
structures replace the original batch-level retry loop:

1. **`not_started`** (deque): first-pass items awaiting their initial submission.
2. **`active`** (dict[Future, shared]): items currently running in the pool.
3. **`retry_heap`** (min-heap keyed by `ready_at`): failed items awaiting backoff.

When a pool slot opens, `_fill_pool` prioritises retry-due items over untouched
first-pass items; retries interleave with the first pass rather than waiting for an
entire batch to complete.
Each item tracks its own `attempt_number` in its `shared` dict; the retry index passed
to `compute_backoff` is `attempt_number - 1` (first retry = index 1 =
`initial_backoff_s`).

The loop condition `while not_started or active or retry_heap` guarantees liveness:
items in backoff are never lost even when `active` is temporarily empty.
`asyncio.wait(FIRST_COMPLETED)` drives completion-order processing, with timeout derived
from the earliest retry_heap entry to wake up promptly for due retries.

The pool exposes `record_retry_scheduled` / `record_retry_consumed` methods that
maintain a `pending_retries` counter in `runpool-status.yaml`. Observability consumers
(`scan_run_status`, `wait_for_completion`, `metaproc pool status`) use this counter plus
`is_pool_alive` to determine whether a run is still active.

### 14.2 Log Compaction

Log compaction strips redundant streaming events from CLI JSONL log files while
preserving all information needed for debugging, cost tracking, and the browser.

#### When It Runs

- **Automatically** after each subprocess exits in `run_parallel` (best-effort via
  `_try_compact_log` -- never fails the run).
- **On log runaway kill** -- compacted immediately after the process is killed.
- **Manually** via the `compact-logs` CLI command (single file or recursive directory).

#### Adapter-Aware Compaction

**Pi CLI** (the primary case): drops `message_update`, `message_start`,
`tool_execution_update`, `turn_start`, `turn_end` events.
Keeps `session`, `agent_start`, `agent_end`, `message_end`, `tool_execution_start/end`,
`auto_retry_start/end`, and anything unrecognized (safe default).

**Thinking preservation**: the last `message_update` before each `message_end` is
buffered. If that update carries thinking content but the `message_end` does not
(DeepSeek/GLM style), a synthetic `message_final` event is emitted.
For models whose `message_end` already carries thinking, no `message_final` is emitted
(avoids duplication).

**Claude and Gemini**: currently pass-through (no compaction needed).

#### File Safety

Uses atomic write with backup.
Original backed up as `.bak`; removed unless `--keep-original`.

#### Idempotency

A compaction header (`{"type": "compaction", "version": 1, ...}`) is prepended.
Files already starting with this header are detected as already-compact and skipped.

#### `CompactionResult`

Reports: path, adapter, original/compacted size and line counts, `already_compact` flag,
plus computed `lines_removed`, `bytes_saved`, `reduction_pct`.

### 14.3 Memory Pressure Monitoring

Cross-platform memory pressure measurement with no external dependencies.
Provides a normalized `MemoryPressure` reading used to gate batch launches.

#### Pressure Levels

| Level | Available Memory | Concurrency Action |
| --- | --- | --- |
| `NORMAL` | >40% | safe to increase |
| `ELEVATED` | 20-40% | hold current |
| `HIGH` | 10-20% | reduce |
| `CRITICAL` | <10% | do not launch new work |

#### Platform Backends

- **macOS**: reads `kern.memorystatus_level` via `sysctl` for free memory percentage;
  reads swap usage from `vm.swapusage`.
- **Linux**: computes `MemAvailable / MemTotal` from `/proc/meminfo`. Optionally refines
  using PSI (Pressure Stall Information) from `/proc/pressure/memory` -- if
  `psi_some_avg10 > 5`, the PSI-derived percentage replaces the meminfo estimate when
  lower.
- **Fallback**: unsupported platforms get 30% (ELEVATED).

#### Integration

Memory pressure is checked **before each batch launch** in `run_parallel`. If CRITICAL,
the system pauses 30 seconds and re-checks.
Pressure readings are logged alongside each batch start for observability.

### 14.4 Pre-Flight Checks

Validates system prerequisites before launching batch work.
Returns a list of `(passed: bool, message: str)` tuples.

#### Checks

- **Disk space**: requires at least 5 GB free (via `shutil.disk_usage`). Always runs.
- **GCP auth**: resolves a GCP access token via `google.auth` (`resolve_gcp_token`).
  Only runs when the adapter is `pi-cli` with a vertex provider (`needs_gcp=True`).

#### Integration

Called from `run_parallel` before the first batch (skipped in dry-run).
Failures abort the run with `CLIError`.

### 14.5 SSE Streaming Anomaly Detection

Detects a known Vertex SSE streaming bug where redundant streaming events inflate log
files to extreme sizes (normal ~2 KB per output token; anomaly inflates to ~12 MB per
token, a 6000x ratio).

#### Detection

- `LOG_RUNAWAY_SIZE_FLOOR = 100 MB` -- files below this are not checked.
- `LOG_RUNAWAY_BYTES_PER_TOKEN = 500 KB/token` -- threshold for anomaly.
- `check_log_runaway(log_path)` reads the file, counts cumulative output tokens from
  `message_end` events, computes bytes-per-token ratio.
  Returns `None` if below floor or no completed messages.

#### Response

When a runaway is detected during the poll loop in `run_parallel`:

1. Kill the subprocess via `SIGTERM` to its process group.
2. Mark as failed with the `log_runaway` error string.
3. Compact the log immediately.
4. The retry classifier treats `log_runaway` as transient, so it retries.

### 14.6 YAML Frontmatter Auto-Repair

Addresses a specific LLM output failure mode: unquoted colons in YAML values (e.g.,
`detail: Strong beat (Note: actually Q1 not Q2)`) that YAML parsers interpret as nested
mappings.

#### Mechanism

`repair_frontmatter_file(path)`:

1. Extract frontmatter between `---` markers.
2. Try `yaml.safe_load`. If it succeeds, return (no repair needed).
3. Scan each line for `key: value` patterns where the value contains unquoted `: `.
4. Wrap problematic values in double quotes (escaping internal quotes).
5. Verify the repaired YAML actually parses.
   If not, skip (no write).
6. Write back the repaired content.

#### Integration

Called on every output file after agent completion but **before validation** in
`run_parallel`. This salvages outputs that would otherwise fail validation due to YAML
parse errors. Applied in both code mode and agent mode paths.

### 14.7 Tool-use Observability

Per-call telemetry for the tool-calling surface: which tools a variant invoked, how many
calls failed with which signature, whether provider-native web search was enabled, and
how many provider rate-limit events blocked the workflow.
Consumed by `write_usage_report` (§15) and by the scaling-validation final report.

**Terminology.** *Native web search* is the provider-neutral term for a model invoking
its built-in web-retrieval path outside the arena-wrapped tool surface: Vertex Gemini
grounding, Anthropic `web_search_*`, OpenAI `web_search_preview`, etc.
*Grounding* is Vertex’s name for its specific path (and the `groundingMetadata` response
field); reserve it for Vertex-specific references.
See the [§14.7 Tool-Use Observability](#147-tool-use-observability) for the full
terminology note.

#### Data-source triad

Three independent sources feed the aggregation:

1. **Tool wrapper invocation logs** (one file per item-event at
   `<phase_dir>/<variant>/<event>/.logs/tools/arena/invocations.jsonl`). Written by the
   downstream tool wrapper; Metaproc only reads it.
   Starts with a config-stub line (`type: config`, `mode`, `backtest_date`,
   `native_web_search`) and continues with one line per tool invocation: `tool_name`,
   `tier`, `exit_code`, `duration_s`, `error`.
2. **pi-cli JSONL logs** at `<phase_dir>/<variant>/.logs/*.jsonl`. The same logs
   consumed by §14.2 Log Compaction.
   The tool-use parser pulls `tool_execution_start` / `tool_execution_end` and
   `rate_limit_event` records.
3. **The `native_web_search` flag on the arena config stub.** Partial-closure signal for
   the native web-search activity visibility gap; see the partial-closure invariant
   below.

Pi-cli and tool wrapper logs cover different layers: pi-cli sees every tool call the
model issued (including the model’s internal retry loops); arena logs see the
wrapper-side outcome of each call (exit code, wall time).
Both feed the same per-variant profile; neither alone is sufficient.

#### Aggregation contract

Three Pydantic models in `src/metaproc/models/usage.py` encode the contract:

| Model | Scope | Key fields |
| --- | --- | --- |
| `ToolCallStats` | Per-tool, inside a `ToolRunProfile` | `tool_name`, `calls`, `ok`, `failures: dict[str, int]`, `duration_s` |
| `ToolRunProfile` | Per-variant, inside a `UsageReport` | `variant`, `records`, `per_tool: dict[str, ToolCallStats]`, `total_configs`, `live_mode_configs`, `native_web_search_configs`, `cutoff_disc_pct` |
| `ProviderRateLimitStats` | Per-`(provider, adapter, variant)` tuple | `provider`, `adapter`, `variant`, `count` (status=blocked events only) |

Invariant (caller-maintained): within every `ToolCallStats`,
`calls == ok + sum(failures.values())`. Models do not validate the invariant so that
aggregators can build up stats incrementally.

#### Failure-kind taxonomy

`src/metaproc/logutil/tool_failures.py` defines `FailureKind`, a nine-member `StrEnum`:

- `ok`: call succeeded.
- `malformed_args`: adapter-side plumbing bug (bad JSON, invalid arg, unrecognized
  argument).
- `tool_timeout`: tool deadline exceeded.
- `tool_error`: tool-side failure (the generic bucket).
- `help_invocation`: adapter hallucinated a deprecated or non-existent tool name and the
  wrapper returned a help banner.
  Distinct from `tool_error` because the remediation is prompt-level, not tool-level.
- `tool_rejected`: the wrapper refused the call before execution (tier policy or auth
  rejection).
- `rate_limit_exhausted`: the tool call hit a provider rate-limit that didn’t clear
  within the retry budget.
- `adapter_dropped_call`: the adapter stripped a `tool_use` block from its response
  stream so downstream never saw the invocation.
  Regression signal from Vertex migration history.
- `unknown`: shape recognised but no classifier rule matched.

Two dispatchers feed it: `classify_arena_tools_record` (arena invocation rows) and
`classify_pi_tool_result` (pi-cli tool-use blocks).
Both fail hard on shape changes instead of silently bucketing as `unknown`.

#### Cutoff-discipline invariant (runbook gap B, closed)

`ToolRunProfile.cutoff_disc_pct = live_mode_configs / total_configs * 100` when
`total_configs > 0`, else `None`. Measures the share of per-item sessions that defaulted
to `mode=live`, `backtest_date=null` instead of launching with a valid pinned
`backtest_date`. Higher values are worse: a session running in live mode can pull
information published after the analysis event it is supposed to predict; that is
future-knowledge leakage on the dataset.
Reported per-variant on every usage report; no one-shot evidence backfill required.

#### Native web-search partial-closure invariant (runbook gap A, partial)

`ToolRunProfile.native_web_search_configs` counts sessions whose config stub carried
`native_web_search: true`. Presence-only signal: tells operators which variants asked
for native web search at dispatch time, but does not capture per-turn activity (search
queries, citations, grounding supports on Vertex).
Per-turn visibility requires a per-provider activity sidecar.
Vertex is the current blocker: the vendored pi-mono adapter strips `groundingMetadata`
before surfacing `AssistantMessage`, so metaproc cannot see it.
Anthropic `web_search_*` and OpenAI `web_search_preview` would need their own parallel
sidecars when those providers move into production.
Tracked in the [§14.7 Tool-Use Observability](#147-tool-use-observability) as the
remaining open gap.

## 15. Usage and Cost Tracking

A dual-view cost tracking system keeps **actual cost** from provider-authoritative
external events separate from **list cost** estimates reported by agent CLIs or computed
from vendor rates. Agent turns, step counts, and retries are never treated as provider
request counts.

### 15.1 Data Model

**`UsageStats`** (engine-level accumulator): tracks `input_tokens`, `output_tokens`,
`cache_read_tokens`, `cache_write_tokens`, `cost_usd`, `duration_s`, `tool_calls`,
`model`, `provider`, plus `cost_is_estimated` flag.

**`UsageReport`** (normalized output): Pydantic model with `totals`, `by_variant`,
`by_model`, `by_provider` -- each a `UsageBucket`. Cost is nested as
`CostPair(actual: CostView, list: CostView)`. Tool-use telemetry rides alongside in
`tool_profiles: dict[str, ToolRunProfile]` and
`rate_limit_stats: list[ProviderRateLimitStats]`; see §14.7 Tool-use Observability for
the full contract.

### 15.2 Pricing Table

Loaded from `metaproc/data/pricing.md` (YAML frontmatter).
Organized as `providers -> models -> actual_price / list_price` with per-1M-token rates
for input, output, cache_read, cache_write.

### 15.3 Adapter-Specific Extraction

- **Pi CLI**: extracts per-turn usage from `agent_end` events, summing across assistant
  messages (`input`, `output`, `cacheRead`, `cacheWrite` tokens and estimated
  `cost.total`).
- **Gemini CLI**: extracts per-model breakdown from `stats.models` or falls back to
  aggregate stats, separating cached input from uncached input and including any
  reasoning residual in billed output exactly once.
- **Claude CLI**: treats nested `modelUsage` as the authoritative whole-attempt usage
  when present.
- **Codex CLI**: treats cached input as a subset of total input for pricing.

### 15.4 Aggregation and Output

`aggregate_usage` performs single-pass accumulation across log files, bucketed by
variant, model, and provider.
Uses dual-cost accumulator to track actual and list costs simultaneously.

`write_usage_report` writes `usage.md` with YAML frontmatter (structured data) plus a
prose summary with markdown tables for provider and model breakdowns.

CLI: `metaproc write-usage <phase-dir>` scans all `.jsonl` files, parses, aggregates,
and writes `usage.md`.

### 15.5 Run Resource Ledger and Terminal Reports

Run-level operational reporting uses one deterministic pipeline:

```text
local source logs and external ResourceEvents
  -> normalized, reconciled resource-events.jsonl
  -> resources.json and resource-usage-summary.md
```

`ResourceEvent` identities come from producer invocation IDs where possible and from
stable evidence fields otherwise.
Equivalent duplicates collapse; conflicting events with one identity fail.
Process step and item lifecycle events attribute elapsed time directly to their owning
hierarchy nodes; the shared process log remains an evidence source rather than a metric
owner. `resources.json` uses the strict standalone `metaproc:ResourcesDocument/0.1`
contract and reports hierarchical metrics, exact `(provider, product, meter, unit)`
quantities, coverage gaps, launch-time budget evaluations, and causal finalization
state. Strict documents carrying the historical `metaproc.resources/v1` or
`metaproc.resources/v2` tokens remain readable.

The first `run-process` launch freezes the recursive process/step topology and
normalized budgets under `.state/run-config.yaml:resources`; resume never rewrites it.
Budgets are observational and do not refuse or terminate work.
The terminal finalizer runs before lease release on success, failure, propagated
timeout, or cancellation.
`metaproc status` may recover missing or stale reports only after the orchestrator is
inactive, using local evidence and the frozen snapshot without provider calls or cached
totals.

`resource-usage-summary.md` stores all structured values in the `resource_usage`
frontmatter envelope and carries the complete SoftSchema contract/schema/envelope/status
description. Its Markdown body is explanatory only.
`metaproc resource-report` and the Metabrowser resource view expose actual cost and list
estimate separately, along with meters, coverage, budgets, and outcome.

## 16. Optional Workspace/State Surface (Future)

An advanced execution-profile feature, not yet implemented.

```yaml
workspace:
  root: .
  isolation: worktree
  writable:
    - train.py
    - experiments/
  commit_policy: explicit
```

Needed for:

- mutation/evaluation loops
- candidate/incumbent comparisons
- autoresearch-style workflows

## 17. Run Pool and Process Management

The `run-parallel` command uses an adaptive, asyncio-based process pool
(`metaproc.runpool`) that replaces the original fixed-batch-size polling loop.

For the full run pool design, including architecture, adaptive concurrency, per-process
health monitoring, kill protocol, observability, and CLI commands, see
[arch-runpool.md](arch-runpool.md).

Key capabilities:

- **Adaptive concurrency**: dynamically grows/shrinks concurrent agent processes based
  on real-time memory pressure, with hysteresis to prevent oscillation
- **Per-process health monitoring**: tracks RSS (including child process trees), wall-
  clock time, log file size, and descendant count; kills runaway processes
- **Atomic status file**: externalizes pool state to `runpool-status.yaml` for
  observability and CLI tools
- **External control**: `metaproc kill` for graceful drain or force-kill via sentinel
  protocol
- **Drop-in integration**: `run-parallel` always uses RunPool for process management

## 18. Run Status and Agent Orchestration Primitives

The `status` and `wait` commands provide structured run monitoring and orchestration for
multi-phase workflows.

- **`metaproc status <run-dir>`**: reads `.state/process-status.yaml` and task status
  files under `.state/tasks/...`, aggregates `ProgressCounts` (completed, running,
  failed, pending, retrying), computes timing statistics and optionally system metrics
  (memory pressure, subprocess count).
  Supports text and JSON output.
- **`metaproc status --check <condition>`**: programmatic check mode for agent
  orchestration: asserts completion state via exit codes (0=passed, 1=failures,
  2=still-running), replacing ad-hoc `--dry-run | grep` patterns.
- **`metaproc wait <run-dir>`**: blocks until a run reaches terminal state, then prints
  final status. Eliminates polling loops in multi-phase playbooks.

Architecture: core logic in `engine/run_status.py` as a Python API; CLI commands are
thin wrappers. The `status` command reads `.state/` artifacts (the same ones
`run-parallel` writes), not JSONL logs.
The `wait` command polls `scan_run_status()` at a configurable interval.

The `runpool` subsystem (section 17) externalizes live process-level details (RSS,
descendant count, kill reasons) via `runpool-status.yaml`. The two layers complement
each other: `status` provides item-level progress; `runpool` provides process-level
health. See [arch-runpool.md](arch-runpool.md) for full run pool design.

## 19. Process Orchestration (`run-process`)

`run-process` is the primary user-facing command for executing a process spec.
It walks the step dependency graph (DAG) in topological order, executing independent
steps in parallel at each level.

### 19.1 Execution Flow

1. Load `ProcessSpec` from the given `.process.md` file.
2. Resolve the spec into a `Plan` via `build_plan()` (variable resolution, fan-out
   expansion, adapter config merges).
3. Validate step references (`--skip`, `--from`, `--only`) against known step IDs.
4. Compute the run directory, write or validate `.state/run-config.yaml`, and acquire
   `.state/orchestrator-lease.yaml`.
5. Compute the active subgraph: if `--from` is set, use `downstream()` to select the
   target step and its transitive dependents; if `--only` is set, restrict execution to
   that single step. Otherwise all steps are active.
6. Compute topological levels via `topo_sort()` restricted to active step IDs.
7. Walk levels sequentially.
   Within each level, execute all runnable steps in parallel via `asyncio.gather()`.
8. After each level, check results: for any failed step, compute `downstream()` and mark
   those steps as `blocked`. If `--continue-on-error` is false, raise immediately on
   first failure.
9. Write `process-status.yaml` after each level and at completion.
10. Write structured events to `process-events.jsonl` via `ProcessEventLogger` (see
    section 9.6).

### 19.2 Step Dispatch

Each step routes based on its mode:

| Mode | Dispatch |
| --- | --- |
| `code` | Execute `handler` (Python callable) or `command` (shell subprocess) |
| `agent` (no fan-out) | Build prompt, launch adapter subprocess, validate outputs |
| `agent` (with `for_each`) | Fan-out via backend (see 19.3) |
| `composite` | Resolve `uses` spec, apply `with` bindings, recurse into child `_orchestrate()` under `{run_dir}/{step_id}/` |
| `manual` | Wait for `.state/manual-ack.yaml`, then validate outputs and publish completion |

Code step stdout/stderr is captured to `{run_dir}/.logs/{step_id}_{ts}.log`.

### 19.3 Fan-Out Backends

Fan-out steps dispatch through one of three backends:

| Backend | Flag | Mechanism |
| --- | --- | --- |
| `local` | `--backend local` (default) | `RunPool` subprocess pool via `run-parallel` |
| `gcp-worker` | `--backend gcp-worker` | Partition items across N worker VMs via GCP Batch (section 21) |

The local backend uses the RunPool (section 17) with step-scoped `.state/` and `.logs/`
directories and an optional external semaphore for cross-step concurrency control.

**Note on backend abstraction:** `local` is a registered `LaunchBackend` implementation
(section 21.8) in the backend registry (`runpool/registry.py`). `gcp-worker` is
different -- it is a multi-VM dispatch mode handled directly in `run-process` via
`dispatch_to_workers()`, not a `LaunchBackend`. It partitions items across N worker VMs,
each of which runs `run-parallel --backend local` internally.
If a second cloud provider were added, a new worker dispatch implementation would
register alongside `gcp-worker` in the `run-process` dispatch logic.

### 19.4 CLI Flags

| Flag | Purpose |
| --- | --- |
| `--var KEY=VALUE` | Parameter bindings (repeatable) |
| `--backend` | Fan-out backend: `local`, `gcp-worker` |
| `--only <step>` | Run only this single step |
| `--from <step>` | Start from this step (ancestors must be completed) |
| `--skip <step>` | Skip specific steps (repeatable) |
| `--force` | Re-run steps even if already completed |
| `--continue-on-error` | Continue executing independent branches on failure |
| `--dry-run` | Show execution plan without running |
| `--cloud` | Submit orchestrator to GCP Batch (section 21) |
| `--num-workers` | Number of worker VMs for `gcp-worker` backend |
| `--machine-type` | GCP machine type for workers |
| `--spot / --no-spot` | Use Spot VMs for workers (default: spot) |
| `--max-concurrency` | Per-pool concurrency limit for fan-out steps |
| `--variant` | Override adapter variant |
| `--adapter-config KEY=VALUE` | Adapter config overrides (repeatable) |
| `--orchestrator-machine-type` | GCP machine type for orchestrator VM (with `--cloud`) |
| `--max-duration` | Max runtime for orchestrator job (e.g., `8h`, `2h30m`, `3600s`) |

### 19.5 Completion and Resumability

`--force` invalidates a step and all its downstream dependents by renaming the relevant
on-disk `status.yaml` files to `.yaml.stale` (via `_invalidate_downstream()`). This
covers both the standard step directory and any output-derived item directories.
Without `--force`, completed steps are detected via task status files under
`.state/tasks/...` and skipped automatically.
Fan-out step completion is determined by `_is_fan_out_completed()`: all items must have
`state == "completed"` in `.state/tasks/{step_id}/{item_key}/status.yaml`. Failed items
do not make the step reusable; only `completed` counts.

`--from` and `--only` require that omitted ancestor steps have already completed
(verified by `_verify_ancestors()`), unless `--force` is set.

### 19.6 Process Status

`run-process` writes `{run_dir}/.state/process-status.yaml` after each level.
The file records per-step state (`pending`, `running`, `completed`, `failed`, `skipped`,
`blocked`) and a derived overall state (`running`, `failed`, `completed`).

## 20. Dependency Graph (`engine/graph.py`)

The dependency graph module provides pure functions for DAG validation and traversal.
No IO, no side effects -- it operates on resolved `ResolvedStep` objects after variable
resolution and fan-out discovery.

### 20.1 `needs` Field

Steps declare dependencies via the `needs` field (a list of step IDs).
The `needs` field is propagated through `build_plan()` and validated by
`validate_step_graph()`.

### 20.2 Graph Functions

| Function | Signature | Purpose |
| --- | --- | --- |
| `validate_step_graph` | `(steps) -> list[str]` | Returns error strings for: duplicate IDs, dangling `needs` references, cycles |
| `detect_cycles` | `(steps) -> list[list[str]]` | DFS-based three-color cycle detection; returns list of cycles (each a list of step IDs) |
| `downstream` | `(steps, root_id) -> list[str]` | BFS traversal returning all transitive dependents of `root_id` (not including `root_id` itself) |
| `topo_sort` | `(steps, step_ids=None) -> list[list[str]]` | Kahn’s algorithm variant returning steps grouped into parallel **levels**; steps within a level have no inter-dependencies and can run concurrently |

`topo_sort` accepts an optional `step_ids` filter to restrict the sort to a subgraph
(used by `--only` and `--from`). Dependencies outside the active set are treated as
already satisfied.
Each level is sorted alphabetically for deterministic execution order.

### 20.3 Integration

- `build_plan()` calls `validate_step_graph()` on the resolved steps and raises on
  errors.
- `run-process` calls `topo_sort()` to compute execution levels, then `downstream()` to
  block dependents on failure and to compute the active subgraph for `--only`/`--from`.
- `--force` calls `downstream()` to invalidate a step and all its transitive dependents.

## 21. Cloud Execution Infrastructure

For the full cloud execution architecture, see
[arch-cloud-execution.md](arch-cloud-execution.md); the integration surface relevant to
the core framework is summarized below.

The cloud execution layer runs metaproc processes on GCP infrastructure using Batch API
for compute and Filestore NFS for shared state.
The design uses the same CLI commands in containers -- no cloud-specific execution logic
exists outside of the dispatch and bootstrap layers.

### 21.1 Architecture

Two-tier execution model:

```text
run-process --cloud
  └── Orchestrator VM (STANDARD, non-Spot)
        └── run-process --backend gcp-worker (no --cloud, avoids recursion)
              ├── Code steps: execute locally on orchestrator
              └── Fan-out steps: dispatch to worker VMs
                    ├── Worker VM 0 (Spot) → run-parallel --backend local
                    ├── Worker VM 1 (Spot) → run-parallel --backend local
                    └── Worker VM N (Spot) → run-parallel --backend local
```

All VMs share a Filestore NFS mount for run outputs, state files, and progress.
The orchestrator uses a STANDARD VM (not Spot) to avoid preemption killing the DAG
coordinator. Workers default to Spot VMs for cost efficiency.

### 21.2 Container Bootstrap (`container_bootstrap.py`)

Shared by worker and orchestrator entrypoints via
`bootstrap_container() -> BootstrapResult`:

1. Read `GH_TOKEN` once, remove it from the environment, and expose it only through a
   temporary askpass helper when a sparse clone needs authentication.
2. Install a current-branch metaproc wheel from `METAPROC_WHEEL_GCS` when set with its
   required `METAPROC_WHEEL_SHA256`, overriding any image-baked metaproc.
3. Acquire the consumer workspace: a `METAPROC_WORKSPACE_GCS` tarball when set, verified
   against its required `METAPROC_WORKSPACE_SHA256`; otherwise a sparse clone of
   `METAPROC_RUN_BRANCH` and `METAPROC_REPO_URL`, falling back to the workspace bundled
   into the image.
4. Editable-install the workspace packages named in the repo-sync payload so consumer
   plugin entry points resolve inside the container.
5. Run each `metaproc.container_bootstrap` entry-point hook so downstream images can
   bootstrap their own tooling.
6. Ensure `RUNS_DIR` exists.
7. Write `~/.pi/agent/models.json` from `METAPROC_PI_MODELS_JSON` if set.
8. Back in the worker and orchestrator entrypoints, invoke each registered adapter’s
   `bootstrap(home)` hook so adapters can materialize any credential files they need
   before the first invocation.
   The `ClaudeCodeCliAdapter` uses this to write `~/.claude/.credentials.json` from
   `CLAUDE_CODE_CREDS_JSON` (bound via Secret Manager; see §21.14), then unsets the env
   var so the OAuth payload does not leak to child processes.
   Adapters that don’t need a credential file leave `bootstrap()` as the default no-op.

### 21.3 Worker Dispatch (`worker_dispatch.py`)

`dispatch_to_workers()` partitions fan-out items across N worker VMs:

- **Partitioning**: round-robin distribution via `partition_items()`.
  `min(num_workers, total_items)` workers are created.
- **Job submission**: one GCP Batch job per worker, each running `worker_entrypoint.py`.
  Items are passed via `METAPROC_WORKER_ITEMS` (comma-separated) and
  `METAPROC_ITEM_CONTEXTS` (JSON array) env vars.
  Large payloads spill to
  `{run_dir}/.state/steps/{step_id}/worker_payloads/worker-<id>-item-contexts.json` on
  Filestore and are loaded via `METAPROC_ITEM_CONTEXTS_FILE`.
- **Resume/adoption**: writes `{run_dir}/.state/steps/{step_id}/dispatch-manifest.yaml`
  after submission so resumes can adopt live workers instead of blindly redispatching.
- **Scaling/reconcile**: uses step-level `scale-state.yaml` plus per-worker
  `claimed-items.yaml` registries during live scale-up.
- **Polling**: async poll loop with configurable interval.
  On failure, reads NFS `runpool-status.yaml` for error detail (failure counts, kill
  reasons). During polling, reads NFS pool status for live progress
  (completed/failed/active counts).
- **Labels**: `metaproc-role=worker`, `metaproc-worker-id=N`, `metaproc-step=<step_id>`,
  plus readable `metaproc-run-id=<sanitized_run_id>` and exact
  `metaproc-run-key=v1-<sha256_prefix>` run identity.
- **Defaults**: `n2-highmem-8` machine type, 50 concurrency per worker, Spot VMs.

`WorkerDispatchConfig` (frozen dataclass): `gcp`, `num_workers`, `max_concurrency`,
`max_retries`, `poll_interval`, `spot`, `variant`, `adapter_config_json`.

### 21.4 Worker Entrypoint (`worker_entrypoint.py`)

Unified container entrypoint for worker containers:

1. Read env vars (`METAPROC_WORKER_ITEMS`, `METAPROC_PROCESS_DIR`, `METAPROC_STEP`,
   etc.).
2. Call `bootstrap_container()`.
3. Build and run:
   `python -m metaproc run-parallel <process_dir> --step <step> --items <items> --backend local [flags]`.
4. Exit with `run-parallel`’s exit code.
   Outputs land on NFS.

### 21.5 Orchestrator Dispatch (`orchestrator_dispatch.py`)

`dispatch_orchestrator()` submits the entire process DAG as a single GCP Batch job:

- Container overrides the Dockerfile ENTRYPOINT to run `orchestrator_entrypoint.py`.
- Uses a STANDARD VM (not Spot) to avoid preemption.
- Forwards all GCP config so the orchestrator can dispatch worker Batch jobs.
- `RUNS_DIR` is set to `<filestore_mount_path>/runs` (e.g., `/mnt/filestore/runs`) when
  Filestore is configured.
  This is the run root, not the bare NFS mount point.
- Labels: `metaproc-role=orchestrator`, readable `metaproc-run-id=<sanitized_run_id>`,
  and exact `metaproc-run-key=v1-<sha256_prefix>`.
- Polls in a while-True loop until terminal state.

`OrchestratorDispatchConfig` (frozen dataclass): `gcp`, `process_dir_rel`, `variables`,
`num_workers`, `worker_machine_type`, `max_concurrency`, `spot_workers`, `variant`,
`adapter_config`, `skip_steps`, `from_step`, `only_step`, `force`, `continue_on_error`,
`orchestrator_machine_type`, `max_duration_s` (default 8h), `poll_interval`.

### 21.6 Orchestrator Entrypoint (`orchestrator_entrypoint.py`)

1. Read orchestrator env vars.
2. Call `bootstrap_container()`.
3. Let the process DAG materialize any items-file/run inputs on NFS via ordinary in-DAG
   code steps.
4. Build and run:
   `python -m metaproc run-process <process_dir> --backend gcp-worker [all forwarded flags]`.
5. Does **not** pass `--cloud` to avoid infinite recursion.
6. Exit with `run-process`’s exit code.

### 21.7 GCP Batch Shared Utilities (`batch_backend.py`)

`batch_backend.py` provides shared configuration and helpers used by both
`worker_dispatch.py` and `orchestrator_dispatch.py` for submitting GCP Batch jobs.

`GCPBatchConfig` (frozen dataclass): `project`, `region`, `container_image`,
`machine_type`, `spot`, `boot_disk_gb`, `max_run_duration_s`, `service_account_email`,
`network`/`subnetwork`, `labels`, `secret_env_vars`, `runs_dir`,
`filestore_server`/`filestore_share`/`filestore_mount_path`, `queue_timeout_s`.

Utility functions: `sanitize_label()` (GCP-safe resource names and readable labels),
`run_identity_label()` / `run_identity_labels()` (fixed-width exact run locator plus
readable compatibility label), `_build_secret_env_vars()` (Secret Manager mapping from
env vars), `build_pi_models_json()` (rewrite pi CLI config for cloud),
`is_transient_api_error()` (retry classification).

### 21.8 LaunchBackend Protocol

The `LaunchBackend` protocol (`runpool/backend.py`) abstracts subprocess lifecycle
within a single machine or VM. Multi-VM cloud dispatch (e.g., `gcp-worker`) is a
separate concern handled in `run-process` (see section 21.3).

```python
class LaunchBackend(Protocol):
    name: str
    async def launch(prepared: PreparedLaunch, label: str) -> LaunchHandle
    async def poll(handle: LaunchHandle) -> int | None
    async def kill(handle: LaunchHandle, sig: int) -> None
    async def health(handle: LaunchHandle) -> HealthMetrics | None
    async def read_log_tail(handle: LaunchHandle, lines: int) -> str
```

Supporting types:

- `PreparedLaunch` (frozen dataclass): `command`, `env`, `cwd`, `log_path`,
  `filter_log`, `metadata` (backend-specific context).
- `LaunchHandle` (frozen dataclass): `pid`, `external_id`, `backend_name`, `metadata`.
- `HealthMetrics` (frozen dataclass): `rss_bytes`, `descendants`, `log_bytes`.

Production implementation: `LocalBackend` (subprocess-based, with RSS/descendant
tracking). `MockBackend` is available for testing.

### 21.9 Monitoring Cloud Runs

- **`gcp status <target>`**: auto-detects local run directory or run-id string.
  Queries Batch API by job name (local) or both exact `metaproc-run-key` and readable
  `metaproc-run-id` (run-id).
  Local display resolves the immutable ID from `run-config.yaml`, then hash-verified job
  metadata, before a path fallback.
  When exact jobs exist, it adds only unkeyed legacy jobs whose structured `RUN_ID`
  verifies as the same run; fully legacy runs retain the readable-label fallback.
  Shows orchestrator and worker jobs with role, state, step, and worker_id.
- **`gcp scale <target> --step <step>`**: updates desired worker topology for an active
  fan-out step by writing `scale-state.yaml` and, when possible, reconciling new worker
  jobs immediately.
- **`gcp logs <target>`**: streams logs from Cloud Logging.
  Auto-detects local dir or run-id and uses the same exact-first job resolution.
- **`gcp cancel <target>`**: cancels all running/queued Batch jobs.
  Auto-detects local dir or run-id and uses the same exact-first job resolution.
  Writes pool kill sentinel if local dir exists.
- **`gcp runs`**: lists metaproc runs across the project.
  Modern jobs group by `metaproc-run-key`; the command recovers `RUN_ID` from
  hash-verified structured `METAPROC_VARS` metadata so exact IDs survive display and
  JSON output. Legacy jobs continue to group by readable label.
- **`gcp resources` / `gcp filestore` / `gcp archive` / `gcp cleanup`**: operator tools
  for cloud inventory, Filestore utilization, long-term run archiving, and terminal-job
  cleanup.
- **NFS progress polling**: during worker dispatch, the orchestrator reads
  `runpool-status.yaml` from NFS to report live progress (completed/failed/active).
- **NFS error extraction**: on worker failure, reads `runpool-status.yaml` for failure
  counts and per-process kill reasons.
- **`gcp remote <command...>`**: runs any metaproc command on the Filestore-connected
  gateway host via SSH/IAP. Bootstraps the full repo on first use
  (`_ensure_remote_repo()`). `--run-id <id>` expands to
  `/mnt/filestore/runs/<id>/<phase>` on the remote host.
  Primary use cases: `stats`, `status`, `pool status`, `tail`.
- **`gcp remote-run`**: launches `run-process` in a tmux session on a remote GCE host,
  so the session survives disconnects and can be reattached or cleaned up later.

### 21.10 Cloud Provider Naming and Extensibility

The cloud layer uses provider-specific names rather than a generic `cloud` abstraction.

**CLI subcommand:** `metaproc gcp` (not `metaproc cloud`). The commands under `gcp` are
inherently GCP-API-specific -- they query GCP Batch API, stream from Cloud Logging,
manage Filestore NFS, etc.
A second cloud provider (e.g., AWS) would get its own subcommand (`metaproc aws`) with
provider-appropriate commands, rather than a single `cloud` subcommand that papers over
real operational differences.

**Framework-level abstraction:** The `LaunchBackend` protocol (section 21.8) and the
backend registry (entry-point group `metaproc.backends`) are fully provider-agnostic.
Adding a new local backend means implementing the 5-method protocol and registering it
-- no changes to `engine/`, `runpool/`, or `models/` are required.

Cloud execution uses a different model: `gcp-worker` is a multi-VM dispatch mode handled
directly in `run-process`, not a `LaunchBackend`. Each worker VM runs
`run-parallel --backend local` internally, so the `LaunchBackend` protocol operates at
the subprocess level within each VM.

The naming hierarchy:

| Layer | Example | Scope |
| --- | --- | --- |
| Framework protocol | `LaunchBackend` | provider-agnostic subprocess lifecycle |
| Backend name | `local` | registered `LaunchBackend` implementation |
| CLI subgroup | `metaproc gcp` | provider-specific operational commands |
| Dispatch mode | `gcp-worker` | provider-specific multi-VM dispatch |

### 21.11 Persistent Infrastructure Decoupling

The framework does not depend on any specific deployment topology.
Persistent infrastructure (VMs, NFS shares, container registries) is external to
metaproc -- the framework provides CLI commands that can be run anywhere.

Design principles for infrastructure dependencies:

- **No infra assumptions in the framework.** The framework never imports or depends on
  knowledge of specific VMs, Filestore instances, or persistent deployments.
  All infrastructure references are configuration, not code.
- **Configuration via environment variables.** All GCP infrastructure parameters
  (`METAPROC_GCP_PROJECT`, `METAPROC_GCP_FILESTORE_SERVER`, `METAPROC_GATEWAY_HOST`,
  etc.) are configurable via env vars and overridable via CLI flags.
  No infrastructure names are hardcoded.
- **The browser is a read-only local tool.** The `serve` command reads filesystem
  artifacts and can be deployed anywhere (local, GCE, Cloud Run, a container on any
  provider) without metaproc caring about the hosting.
- **Cloud commands are operational tools, not control plane.** The `gcp` subcommands
  query and manage cloud resources but do not constitute a required control plane.
  A local `run-process` produces the same results as a cloud one.

### 21.12 Filesystem-First Resume Contract

Authoritative run state lives only on the run filesystem -- local disk for full-local
runs, Filestore NFS for all cloud-backed modes.

**`run-config.yaml`** (`{run_dir}/.state/run-config.yaml`): written at run creation time
with immutable run identity (process name, run_id, backend, variant, git SHA, creation
timestamp). On resume, validated against current launch parameters -- process identity
and run directory must match.
Cross-topology resume (e.g. hybrid to full cloud) is allowed because they share the same
authoritative filesystem.

**`orchestrator-lease.yaml`** (`{run_dir}/.state/orchestrator-lease.yaml`): records the
current orchestrator owner and heartbeat so a second orchestrator will refuse to start
unless the lease is stale or explicitly taken over.

**Cloud fan-out state** also lives on the run filesystem:

- `{run_dir}/.state/steps/{step_id}/dispatch-manifest.yaml` for worker-job adoption on
  resume
- `{run_dir}/.state/steps/{step_id}/worker-<id>/claimed-items.yaml` for live scale-up
  item ownership
- `{run_dir}/.state/steps/{step_id}/scale-state.yaml` and `scale-override.yaml` for
  desired topology and operator caps

Resume behavior: re-running `run-process` with the same `RUN_ID` skips completed steps
and items based on on-disk status records.
`run-config.yaml` prevents accidental collision between unrelated runs sharing a
directory.

### 21.13 Mount Path Standardization

All VM types (workers, orchestrators, browser host) mount the Filestore NFS share at
`/mnt/filestore` by default.
`RUNS_DIR` resolves to `<mount_path>/runs`, not the bare share root, so run trees live
at `/mnt/filestore/runs/{run_id}/`. The mount path is a container-level Volume mount
point set via the Batch API Volume spec, not subject to COS host-level path
restrictions.

### 21.14 Secret Manager Integration

**Design rationale (authoritative mechanism in
[arch-cloud-execution.md §3.10](arch-cloud-execution.md) and
[arch-authentication.md](arch-authentication.md), Secret Manager registry / UC-9 /
UC-10):** any credential delivered to a Batch job is injected via Secret Manager rather
than as a plaintext env var, because `gcloud batch jobs describe` would otherwise return
the plaintext value as part of the job spec.
The `GCP_SECRET_REFS` registry in `cloud/gcp/batch_backend.py` centralizes the
`(plaintext_env, secret_env, description)` rows so adding a new credential is a one-row
change. `resolve_gcp_secret_ref()` enforces the anti-leakage invariant: setting the
plaintext env without the Secret Manager ref fails dispatch up front.

## Future Considerations

### Open Questions

- The Plan schema is now at `metaproc:Plan/0.6` (adds reporting-only `resource_budgets`;
  0.5 added `lane_matrix` and `ExecutionLane`). The lane execution model is not yet
  documented in this arch doc.
  [unverified] whether lane-based dispatch is fully integrated into `run-process` or
  still under development.
- `overrides.yaml` (operator escape hatches via `metaproc override`) is referenced in
  the runtime state inventory (section 5.1) but not covered in its own subsection.
  The interaction between overrides and the resume/fingerprint system (section 10) is
  undocumented.
- Several newer CLI commands (`liveness-watch`, `resume-daemon`, `run-manifest`,
  `softschema`, `trace`) lack design-level documentation in this doc.
  Their operational semantics are only in code docstrings.
- The `codex-cli` adapter section (§12.2) is thorough but the Codex adapter is
  relatively new. [unverified] whether all described auth modes have been validated
  end-to-end in production cloud runs.

### Potential Improvements

- Extract the per-adapter reference (§12.2) into a separate adapter-catalog doc as the
  adapter count grows, keeping this doc focused on the contract and wire format.
- The illustrative downstream profile (§7) could move to an application-profile doc,
  leaving this doc strictly framework-scoped.
- Add a “Reading Guide” section at the top to help readers navigate the more than 21
  sections by use case (operator, process author, adapter implementer, framework
  contributor).
- Consolidate the cloud execution summary (§21) further: much of its content is now
  covered in [arch-cloud-execution.md](arch-cloud-execution.md), and the duplication
  creates maintenance burden.
- Document the `dispatch` subsystem (slot coordinator, credential pool) which is
  referenced by the adapter registry but not covered in this doc.
  See `src/metaproc/dispatch/` for the implementation.

See also [metaproc-design-rev3-proposals.md](../metaproc-design-rev3-proposals.md) for
the original future-work backlog.

* * *

## Revision History

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
- **Section 21.14**: generalized from GH_TOKEN-only to the `GCP_SECRET_REFS` registry
  pattern in `batch_backend.py`; documents `resolve_gcp_secret_ref()` and the
  plaintext-refusal policy that applies uniformly to every row.
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
