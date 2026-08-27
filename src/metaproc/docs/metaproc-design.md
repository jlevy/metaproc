---
title: Metaproc Design
description: "How Metaproc is built, in detail: the spec format, the resolved plan, runtime artifacts, resumability, the adapter contract, the plugin protocol, and the robustness subsystems. The second document to read, after the concepts doc."
author: metaproc team
status: Approved
---
# Metaproc Design

**Date:** 2026-03-23 (last updated 2026-08-25) **Status:** Approved

Also readable as `metaproc help design`.

Implementation reference for Metaproc, covering how the conceptual model defined in
[metaproc-concepts-and-principles.md](metaproc-concepts-and-principles.md) is realized
in code: spec format, runtime artifacts, CLI commands, adapter wire formats, plugin
protocol, and robustness subsystems.
Companion architecture documents cover one subsystem each and ship alongside this one:
[arch-runpool.md](arch-runpool.md), [arch-cloud-execution.md](arch-cloud-execution.md),
[arch-authentication.md](arch-authentication.md),
[arch-claude-code-harness.md](arch-claude-code-harness.md),
[arch-execution-model.md](arch-execution-model.md),
[arch-file-io-utilities.md](arch-file-io-utilities.md), and
[arch-testing.md](arch-testing.md).

Additional reference docs: [conventions.md](conventions.md) (naming rules),
[credential-setup.runbook.md](credential-setup.runbook.md) (auth), and
[artifact-catalog.md](artifact-catalog.md) (every runtime artifact).

Examples in this document use the fictitious `example_plugin` namespace to show where
consumer-owned processes, schemas, handlers, and artifacts belong.
Metaproc does not ship that package or its domain behavior.

## Reading Guide

This document has more than twenty numbered sections.
Almost nobody needs all of them at once, so start from what you are doing:

| You are… | Read |
| --- | --- |
| **authoring a process spec** | §6 Authored Process Model — the envelope, step modes, inputs and outputs, `for_each`, template resolution, and the step field reference in §6.13 |
| **running or debugging a run** | §9 Runtime Model (what each artifact means), then §10 Resumability and Publication Semantics (why a step did or did not re-run). [metaproc-operator-reference.md](metaproc-operator-reference.md) is the task-oriented version |
| **implementing an adapter** | §12 Adapter Contract, then §14 Robustness Subsystems for failure classification, and §15 for usage and cost attribution |
| **contributing to the framework** | §5 Implementation Inventory for the map, then §8 Resolved Plan Model and §19 Process Orchestration for the execution path |
| **reviewing a design decision** | §8.1 Why the Plan Is Data, §10 Resumability, and §11.1 on why an items file is a candidate source rather than completion state |

Two conventions worth knowing before you start.
Section numbers are stable identifiers, not an outline: numbering begins at 5 because
the first four sections became
[metaproc-concepts-and-principles.md](metaproc-concepts-and-principles.md), and a
section that moves out keeps its number reserved rather than renumbering the rest.
Subsystems with their own document — the run pool, cloud execution, authentication, the
Claude Code harness, file IO, the execution model, and testing — are summarized here and
specified there; where the two differ, the companion document wins.

## Scope and Imported Concepts

Terminology and principles live in
[metaproc-concepts-and-principles.md](metaproc-concepts-and-principles.md); read it
first for the definitions assumed below.
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

The same process spec supports local and full-cloud topologies.
Full-cloud execution moves both the orchestrator and run pool into Batch; the operator
host is not part of the runtime path.
See [arch-cloud-execution.md §2.2](arch-cloud-execution.md) for the full topology table.

See § 19 for orchestrator details, § 21 for cloud execution.

## 5. Implementation Inventory

The framework spans three abstraction profiles defined in
[metaproc-concepts-and-principles.md §3.4](metaproc-concepts-and-principles.md): **core
model**, **execution profile**, and **application profile**. The conceptual definitions
live there. The inventory below lists the authored files, package subsystems, plugin
layer, and emitted runtime artifacts that realize each profile.
For the per-artifact reference (filename, format, schema, lifecycle, writer, readers),
see [artifact-catalog.md](artifact-catalog.md); for format-selection rules, see
[conventions.md §File Format Policy](conventions.md#file-format-policy).

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
pool status / pool events
gcp run / gcp status / gcp scale / gcp logs / gcp cancel / gcp runs / gcp resources / gcp filestore / gcp cleanup
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
schema_conform (contract-directed scalar quoting for agent-written YAML)
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
For compatibility, the child currently inherits the parent variable namespace and `with`
overlays the bindings that form the authored child interface.
New processes should treat `with` as the public boundary; narrowing the inherited
namespace remains a separate compatibility change.
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
- `for_each`

With `for_each`, Metaproc maps one in-process child scope per item under
`<run>/<step>/<item-key>/`. The mapped parent task remains in
`<run>/.state/tasks/<step>/<item-key>/`; it completes only after the child process and
the mapped step’s declared outputs validate.
The child declares all outputs required for its own completion, while the parent
declares the subset published downstream.
Automatic child-port projection is not yet implemented.
Resume revalidates both boundaries before reusing a completed mapped item.

Mapped scopes share the root `RunExecutionContext`; they do not launch `run-process`,
acquire child orchestrator leases, or hold an executable-leaf permit while waiting
between child stages.
`for_each.max_concurrency` limits active structural scope evaluators, not executable
leaves. Child leaf retry policies remain available, while a whole-scope `for_each.retry`
is rejected. Mapped composites are single-host and reject the `gcp-worker` backend until
a multi-host mapping contract exists.

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

Handler signature: `def handler(context: StepContext, step_config: StepConfig) -> None`.
`StepContext` is a `dict[str, str]` subclass containing resolved input variables (the
same resolution as `mode: agent`). Long-running handlers should call
`context.cancel_requested()` at safe checkpoints and return when it becomes true.
The handler writes outputs directly; the engine records `.state/` completion markers.
If the handler raises, the step fails with the same state recording as a failed agent
step.

`mode: code` supports `for_each`. Each item is one invocation with its own resolved
context, so per-item state, logs, and artifacts address the item rather than the step,
and one item failing does not cancel its siblings: every item is awaited and the step
succeeds only if all of them did.
Item discovery is the same execution-time roster read the agent path uses; nothing else
is shared, because adapters, variants, and auth pools have no meaning for a handler.

Handlers run off the event loop.
A handler is a synchronous callable, and calling it inline would pin the loop for its
whole duration, which serializes every sibling item of a fan-out no matter how the
dispatcher gathers them.
Handlers therefore need not be thread-safe against themselves, but a fan-out runs
several concurrently, so a handler sharing mutable process state across items must guard
it. Command-backed code steps use the same run-owned executor and concurrency gates.
Their subprocesses share the process directory, so an authored command that mutates
shared repository state, lockfiles, or undeclared files must provide its own
synchronization or write only to per-item paths.

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
| `contract` | Contract ID the artifact must validate against, as `namespace:Name/vN`. Checked at the step boundary whatever the format: a `frontmatter-md` output validates its frontmatter, any other its document root. Spelled `schema` in older specs, which still parse. |
| `on_invalid` | What it costs when this output fails its contract, keyed by invariant, contract ID, or failure kind, most-specific-first: `fail`, `retry`, or `fail_run`. Governs the output that declares it and no other. |

**A contract ID is not a schema path.** The two are easy to conflate because both are
called schemas in casual use, and keeping them apart is what makes the boundary check
work:

- A **contract ID** is an identity, such as `example:Record/v1`. It resolves through the
  plugin registry to a Pydantic model, and that model is what validates the document.
  This is what an output declares and what a document’s `softschema.contract` names.
- A **schema document** is a generated JSON Schema file.
  It is compiled *from* the model with `softschema compile`, committed for portability
  and inspection, and kept honest by a drift check.
  A document may point at one through `softschema.schema`, and nothing about validation
  depends on that pointer: an artifact validates identically whether the path is right,
  wrong, or absent, because the contract ID is what does the resolving.

So the Pydantic model is the authority, the contract ID is how it is named, and the
schema document is a derived artifact.
Writing a new contract means writing a model and registering it, not authoring a schema
file by hand.

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

Primary use: bind named child-process inputs for `mode: composite`. It is the authored
child interface and new processes should not depend on undeclared parent values.
For compatibility, the current runtime still starts a child with the parent variable
namespace and overlays `with`; restricting that inherited namespace is a separate,
potentially breaking change.
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
| `for_each` | agent, code, composite | items-file-driven fan-out declaration |
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

This section held a worked profile from one downstream analysis domain — the Predict,
Retro, Mine, and Learn processes — as an illustration of how the authored model above is
used in practice.

It has moved to
[the analysis reference profile](https://github.com/jlevy/metaproc/blob/main/docs/project/design/metaproc-analysis-profile.md).
Metaproc core is consumer-agnostic: domain process specs, schemas, handlers, and
fixtures belong to the packages that own them, and this document ships to all of them.
The section numbering is kept so cross-references from other documents stay valid.

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
Cloud worker-VM fan-out is triggered via `run-process --backend gcp-worker --cloud`. The
bare `gcp-worker` form is reserved for the inner Batch orchestrator leg, including
direct `run-parallel` plumbing.
That is the current implementation syntax, not the target public topology model.
The planned interface exposes orchestrator and worker placement independently as
`--orchestrator` and `--worker`; section 21.1 defines the resolution boundary.

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
  Outside GCP Batch, `gcp-worker` must be paired with `--cloud`.
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
- `gcp run` -- run one lower-level command in a single Batch task
- `gcp resources` -- show metaproc-related GCP assets via Cloud Asset Inventory
- `gcp filestore` -- inspect Filestore instance status and utilization
- `gcp cleanup` -- delete old terminal-state Batch jobs

**Pool subcommands (`metaproc pool ...`):**

- `pool status` -- show RunPool live status (concurrency, pressure, active processes)
- `pool events` -- show RunPool event log (starts, exits, kills, pressure checks)

The `qa` surface is intentionally not a standalone framework command: QA remains
process-owned and is typically expressed as ordinary steps inside the DAG.

## 9. Runtime Model

The authored model stays small.
The emitted runtime model is explicit.

The four artifact groups (run-level state, per-step state, per-task state, logs)
populate the `.state/` and `.logs/` branches at every scope root.
For the per-file reference (filename, format, schema, lifecycle, writer, and readers),
see [artifact-catalog.md](artifact-catalog.md).
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
[metaproc-operator-reference.md](metaproc-operator-reference.md).

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

Fan-out applies to `mode: agent`, `mode: code`, and `mode: composite`. All three share
neutral item discovery, resolved-key validation, per-item addressing, and the
`run_fan_out` runner.
Their invocation paths remain distinct: agent work carries adapters, execution profiles,
and auth-pool dispatch; code work invokes a handler or command; composite work
recursively evaluates a child spec in-process.
This closes the need for a consumer code handler that launches a child Metaproc CLI,
without adding the larger invoker abstraction described in proposal P8.

Duplicate resolved item keys are rejected before execution because they would address
the same task, log, artifact, and child-scope namespace.
A mapped composite is local to one orchestration host, writes its child scope under
`<run>/<step>/<item-key>/`, and shares the root execution context.
Its optional `max_concurrency` limits active scope evaluators; executable child leaves
remain governed by the run-level admission authorities.

Every field named in `bind_fields` must be present and non-empty on every item.
There is no optional dispatch field, so a roster where a field applies to only some
items carries an explicit sentinel value rather than omitting it.

### Fan-In Collections

An input declaring `collect: <step>` receives that fan-out step’s per-item outcomes as
one manifest (`metaproc:FanInOutcomes/0.1`) instead of rediscovering upstream state by
walking directories.
Each record carries the item key, its terminal state, whether it succeeded, and its
error where there is one.

`require:` states which outcomes satisfy the edge.
`succeeded` needs every item to have succeeded.
`finished` accepts any terminal outcome, so a partially failed upstream still satisfies
the edge and the consumer decides what a failure means.
The two are named for the condition each states, because “completed” reads as terminal
in some contexts and as success in others.

`require: finished` also governs blocking: a consumer declaring it is not blocked when
the failure lies at the collected step or anywhere feeding it, since an item dying two
stages back is exactly why the collection has partial coverage.
This tolerance is evaluated over the consumer’s affected direct dependencies.
An independent required dependency that did not descend from the failure remains
satisfied, but a second required dependency that did descend from the same failure still
blocks the consumer; one tolerant collection never rewrites another edge’s contract.
Two authored clauses that name the identical upstream currently collapse to one
`ResolvedStep.needs` entry, so Metaproc cannot yet distinguish a tolerant collection and
a strict requirement on that same upstream.
Author a distinct intermediate dependency when both contracts are required; widening the
resolved dependency model remains evidence-triggered work.

Where an item failed a contract, its record carries the structured failure alongside the
message: the failing `invariant`, its `location` in the document, the `contract` the
output declared, and the `kind` of refusal.
A consumer routing work by owner needs that distinction, because a missing output and a
refused invariant are different people’s problems and the rendered sentence cannot
express the difference.

An item that never reached the collected step is reported with where it stopped and that
step’s failure detail, rather than as a bare absence.
A ticker that raised and a ticker that silently produced nothing are different problems
with different owners, and the collection is where a consumer learns which it has.

The manifest reports against the collected step’s **expected roster**, not against the
task directories on disk.
An item that died upstream never creates a directory there, so a collection over what
arrived would report three of four items as full coverage; reporting against the roster
distinguishes succeeded, failed, and never-reached.
The manifest is derived from durable per-item state on every read and never stored as
truth, so it cannot drift from the state it describes.

### Declared Retry on the Code Path

A `mode: code` step honors the same contract-layer declarations the pool path honors.
`for_each.retry` supplies the budget and `outputs.<name>.on_invalid` supplies the
verdict, resolved by `classify_output_failures` from the invariant that refused the
output rather than the sentence describing it.
Nothing in the executor interprets a failure: the producing output declares what its own
contract failure costs, and the loop honors it.

The budget is resolved per step, not once per caller.
A chain runs several steps and the budget is declared on the step with a reason to
retry, so applying the first step’s policy to the whole walk would give a stage a budget
nobody declared for it and withhold one that was declared.

A failure with no structured record is not a contract failure and is left alone, since
the operational classifier owns those.
A step declaring no policy is invoked once, which is the behavior of every spec that
declares nothing.

Unlike the non-fan-out agent loop, which records `attempt: N` in the step’s status, this
path does not thread the attempt number into the status writer: every invocation writes
`attempt: 1`, so the durable record undercounts a retried task.
The replay harness pins this
(`test_the_durable_record_undercounts_the_retries_it_made`), and durable attempt records
are what remove it.

Every failed attempt records its `failure_class` on the durable per-item record:
`invalid_output` for a contract failure, and `classify_failure`’s verdict for an
operational one. Placement (design test 17) therefore holds on the code path, not only
where pool events are emitted.

### Per-Step Concurrency

`for_each.max_concurrency` bounds one step’s items in flight, independent of any other
step’s. A run-wide cap and an execution profile both answer different questions, the
first being the whole run’s budget and the second which adapter and model, so expressing
a per-step ceiling through either conflates it with something else and leaves the limit
invisible in the spec that describes the work.

Both limits bind and the smaller wins: a step ceiling above the run cap cannot mean
exceed the budget, and one below it is a real constraint the run cap must not override.
In a chain the gate is per step rather than shared across the walk, so a tight ceiling
on one stage does not throttle the others an item passes through.
Omitted, the step is bounded only by the run-wide cap.

### Item-Aligned Chains

`for_each.align: same_key` declares a step’s `needs` edge item-scoped rather than
step-scoped: this step’s task for item *k* waits only on the upstream task for item *k*.
Consecutive code steps carrying it form a chain that executes once per item instead of
once per step, so an item advances as soon as its own predecessor commits rather than
waiting for the slowest sibling.

`graph.item_aligned_chains` decides where this applies, and refuses more than it
accepts. Alignment requires that the upstream also fan out over the *same resolved
source*, because matching key strings across unrelated rosters is coincidence rather
than identity. A step needing anything outside the chain ends it, since that edge is
genuinely step-scoped.
Two steps aligning to one upstream leave both edges step-scoped: item-scoped forks are
meaningful but a linear chain cannot express one, and resolving it by first-wins would
make the result depend on step order in the spec.

Failure follows the same granularity.
An item failing partway through a chain skips its own remaining steps and touches no
sibling, so the chain finishes with partial coverage instead of blocking the graph.
Measured on a four-item cohort where one item fails at the second of three stages: under
the level walk no item completes the third stage, because the step failure blocks it
wholesale; under an aligned chain three of four complete.

Resume is per item and per step.
An item that finished the head but not a later step is still actionable, so the chain
discovers against the whole chain rather than the head and skips only the steps already
completed for that item.
Filtering on the head’s completion would drop such an item from the chain entirely and a
resumed run would silently do nothing for it.
Work that was in flight when a run died restarts, since it never committed.
The current level-walk integration and its two completion views are described in
[arch-execution-model.md § How Item-Aligned Resume Works Today](arch-execution-model.md#how-item-aligned-resume-works-today).

Absent `align`, nothing changes.
The edge stays step-scoped and the level walk executes it exactly as before, which is
the compatibility floor for every existing spec.

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
     `CLAUDE_CODE_CREDS_JSON` through container-side hydration (see §21.14); the
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
     `METAPROC_GCP_SECRET_CODEX_CREDS` → `CODEX_CREDS_JSON` as a Batch container-side
     hydration binding; the adapter’s `bootstrap(home)` materializes
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

### Failure Layers as Implemented

The concepts doc’s failure layers (operational, contract, domain) map onto two
classifiers and one enum:

- **`FailureClass`** is the per-item class: `rate_limited`, `quota_exhausted`,
  `server_error`, `timeout`, and `crash` are the operational layer; `invalid_output` is
  the contract layer’s single entry, subdivided by `OutputFailureKind` (missing, empty,
  unreadable, structural, semantic); `unknown` is the unrecognized remainder.
- **`classify_error`** decides operational retriability from the rendered error string:
  a permanent blocklist first, a transient list second, bare `exit code N` retries, and
  wholly unrecognized text fails.
  It survives for records written before structured failures existed; contract failures
  should route through `classify_output_failures`, which reads what refused the output
  instead of the sentence describing it.
- Unknown operational failures default to non-retriable, which is the deliberate reading
  of the concepts doc’s rule: silently retrying an unrecognized failure hides a new
  failure mode.

Handling follows the class: retriable classes retry per `RetryPolicy` on the pool path,
and `quota_exhausted` pauses submissions until the provider’s named reset time rather
than burning attempts against a closed window.

Two known gaps, both against design test 17: the `run-process` inline execution paths
neither classify nor retry (the pool path owns that machinery today), and no aggregate
view buckets failures by layer or class, so the real-time half of placement exists and
the aggregate half does not.

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

A subprocess is opaque -- its exit code is always 1 for failures -- so for anything it
reports, the error string from `status.yaml` is the only signal there is.
`classify_error()` reads it, and owns rules 1-3 and 5 below.
Rule 4 is the exception: an output failing its contract is refused by the framework
itself, which therefore holds a structured record and does not have to read its own
prose back. `classify_output_failures()` owns that one.

1. **Permanent patterns** (checked first): `enospc`, `enomem`, `quota`,
   `permission denied`, `billing`, `credits`, exit code 137/143, `cancelled` -- always
   `FAIL`.
2. **Transient patterns**: `timeout`, `rate limit`, `429`, `truncated_headers`,
   `gcloud auth`, `unavailable`, `503`/`502`, `econnrefused`/`econnreset`, `connection`,
   `log_runaway` -- produce `RETRY`.
3. **Bare “exit code N”** -- default to `RETRY` (most are transient API errors).
4. **Output validation failure** -- decided from the structured failure record, not from
   the error string. `missing`, `empty`, and `unreadable` are `RETRY`, because another
   attempt may produce what this one did not; `structural` and `semantic` are `FAIL`,
   because a document the contract refuses will be refused again identically.
   An output’s `on_invalid` overrides its own failures.
5. **Default** -- `FAIL`.

Rule 4 was previously a substring test over the formatted error, looking for `schema`,
`envelope`, or `mismatch`. Because the sentence contains the artifact’s filename, the
test read the filename too, and two outputs missing for the same transient reason drew
opposite verdicts:

```text
output validation failed: schema-manifest.md: file not found                    -> FAIL
output validation failed: source-snapshot.md: file not found                    -> RETRY
```

The rule now reads `OutputFailureKind`, which validation already knew and used to
discard. `classify_error` keeps the substring path for status records written before
structured failures were stored; nothing new should reach for it.

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

`RetryPolicy` is a Pydantic model with four fields, retrying by default:

| Field | Default | Purpose |
| --- | --- | --- |
| `max_retries` | 12 | Maximum retry attempts per item |
| `initial_backoff_s` | 5.0 | First retry delay |
| `backoff_multiplier` | 1.5 | Exponential multiplier |
| `max_backoff_s` | 600.0 | Backoff cap |

Backoff: `initial_backoff_s * (backoff_multiplier ^ (attempt - 1))`, capped at
`max_backoff_s`. Content failures (`INVALID_OUTPUT`) re-run the authored prompt against
the same inputs with the latest structured validation facts appended.
The original prompt remains an exact prefix; output, kind, path, contract, invariant,
location, and message are JSON-quoted in a framework-authored correction section.
The section bounds individual values, total size, and failure count so a pathological
validator result cannot consume the next attempt’s context window; it records how many
failures were omitted.
JSON-escaped values have their own rendered-size ceiling, so control-heavy text cannot
crowd every actionable coordinate out of the section.
Subprocess and transport failures never create or replace this section because they
produced no validation facts; a later retry retains any still-pending feedback from an
earlier content failure.
The content-failure budget is capped separately at `MAX_CONTENT_FAILURE_RETRIES_DEFAULT`
(3) however large `max_retries` is.

#### Policy Resolution

The retry policy resolves through a priority chain:

1. `--no-retry` CLI flag (disables retry entirely)
2. `--max-retries` CLI override
3. step-level `for_each.retry`
4. process-level `defaults.retry`
5. `RetryPolicy()` (framework default: on, `max_retries=12`)

A spec that declares no `retry:` block therefore still retries.
A step that must fail fast opts out per output
(`on_invalid: {missing: fail, empty: fail, unreadable: fail}`) or per policy
(`retry: {max_retries: 0}` under `defaults`).

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
`initial_backoff_s`). An item also carries its latest `output_failure_feedback`. The
scheduler replaces that field only after a retryable validation failure, and
`_build_prepare_launch` appends it to the next attempt’s resolved prompt.
A transient retry before any content failure therefore receives the original prompt
unchanged.
Attempt-numbered prompt snapshots preserve what each launch received even when
zero-backoff retries start within the same second.

The loop condition `while not_started or active or retry_heap` guarantees liveness:
items in backoff are never lost even when `active` is temporarily empty.
`asyncio.wait(FIRST_COMPLETED)` drives completion-order processing, with timeout derived
from the earliest retry_heap entry to wake up promptly for due retries.

Non-fan-out agent steps run the same content-failure loop inline in
`run_process._execute_agent_step`: declared outputs are repaired, conformed and
validated after each attempt (§14.6), `classify_output_failures` reads the structured
failure record against the output’s `on_invalid`, and retryable verdicts re-run the step
under the `INVALID_OUTPUT` cap with the same correction section used by fan-out retries,
recording `attempt: N` in the step’s status.
Nonzero exits are classified exactly as fan-out failures are -- transient ones draw the
full `max_retries` budget, with the log tail folded into the recorded error -- while
step timeouts and write-boundary violations stay terminal on this path.

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
| `NORMAL` | >25% | ramp concurrency up |
| `ELEVATED` | 15-25% | hold current concurrency |
| `HIGH` | 8-15% | reduce |
| `CRITICAL` | <8% | do not launch new work |

The thresholds sit lower than intuition suggests, deliberately.
A workstation running a browser and an editor idles around 30-50% available, and
treating that as cause for caution would hold concurrency down during normal operation.
`_classify_available` in `osutils/memory_pressure.py` is the source of truth.

#### Platform Backends

- **macOS**: budgets from `vm_stat` reclaimable pages, free plus inactive plus
  purgeable, over `hw.memsize`, using the page size `vm_stat` reports.
  `kern.memorystatus_level` is read alongside it and carried as `alarm_pct`, never as
  the budget: it counts active pages, so it runs roughly 2x the reclaimable figure.
  Swap comes from `vm.swapusage`. See
  [memory-accounting-reference.md](https://github.com/jlevy/metaproc/blob/main/docs/memory-accounting-reference.md).
- **Linux**: computes `MemAvailable / MemTotal` from `/proc/meminfo`. Optionally refines
  using PSI (Pressure Stall Information) from `/proc/pressure/memory` -- if
  `psi_some_avg10 > 5`, the PSI-derived percentage replaces the meminfo estimate when
  lower.
- **Unsupported platforms**: `measure()` raises `UnsupportedTelemetryPlatformError`. A
  pool that cannot read memory is not a pool that should guess at a safe-looking number
  and launch anyway.

#### Integration

Memory pressure is consumed in one place: `RunPool._monitor_loop`, which samples on
every tick and passes the resulting level to `_adjust_concurrency`. There is no
per-batch check in `run_parallel` and no pause on CRITICAL; CRITICAL reduces the memory
ceiling by 50% on each tick, and the reduction is non-preemptive, so processes already
running are left alone and the narrower ceiling applies to what launches next.

The level is the only thing that crosses that boundary.
`_adjust_concurrency` never sees a byte count, which is why a starting estimate made
from a wrong budget is not corrected by later sampling.
See [arch-runpool.md](arch-runpool.md) for the policy table and the ramp factors.

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

### 14.6 Agent-Artifact Repair Passes

Two passes run over a freshly emitted agent artifact, in order, before its declared
outputs are validated.
They answer different questions and are deliberately kept apart: repair asks whether the
document is YAML at all, conform asks whether it says the types its contract names.

Both are scoped to **agent-authored outputs only**, which is two call sites and no
others: `run_parallel._handle_success`, where the agent fan-out lands a successful item,
and `run_process._execute_agent_step`. Neither runs on a code branch -- not
`run_process._execute_code_step`, and not `run_parallel`’s own `mode: code` batch loop.
A code handler builds its artifact from typed values through a real writer, so a
document that will not parse there is a bug in the handler, wrong for every item rather
than for this one, and repairing it would launder a serializer defect into a clean run.

The scoping is asserted rather than described, by `TestWhichExecutorsRewriteAgentOutput`
in `tests/test_yaml_repair.py`, from three angles: a `mode: code` fan-out run whose
handler emits unparsable frontmatter must leave the document byte-identical and fail the
item; neither executor’s code branch may call either pass, read positionally off the
parse tree so the helper spelling does not matter; and neither executor may stop calling
the two helpers on its agent path, or reach past them to `repair_frontmatter_file`.

Both resolve a declared output path through the shared
`validation.resolve_output_fpath`, so every pass over an output names the same file.
Path resolution is shared; existence probing is not.
Both rewriting passes read and write plain text, so neither follows validation’s `.gz`
sibling, and a compressed artifact is validated without being repaired or conformed.

#### Pass 1: YAML frontmatter auto-repair

Addresses a specific LLM output failure mode: unquoted colons in YAML values (e.g.,
`detail: Strong beat (Note: actually Q1 not Q2)`) that YAML parsers interpret as nested
mappings.

`repair_frontmatter_file(path)`:

1. Extract frontmatter between `---` markers.
2. Try `_ruamel_safe_load`. If it succeeds, return (no repair needed).
   The pre-check deliberately uses the downstream validator’s parser: a document that
   passes PyYAML can still fail `ruamel.yaml`, and when the two disagreed the operator
   saw “Repaired YAML” followed by `invalid_outputs`.
3. Scan each line for `key: value` patterns where the value contains unquoted `: `.
4. Wrap problematic values in double quotes (escaping internal quotes).
5. Verify the repaired YAML actually parses.
   If not, skip (no write).
6. Write back the repaired content.

#### Pass 2: contract-directed scalar conform

Addresses the failure mode a parseable document still has: a YAML plain scalar carries
no type marker, so a brand genuinely named `1850` arrives as an integer and fails a
`type: string` contract.
An agent writing frontmatter by hand has no serializer in the path to quote it.

`conform_declared_outputs(item_dir, outputs, variables=..., registry=...)`, in
`engine/schema_conform.py`, borrows both halves rather than reimplementing either:

1. **The contract’s own model says what is wrong.** The payload is validated with the
   same pydantic model that will judge it seconds later, and the only errors acted on
   are `string_type` -- pydantic’s way of saying a string belongs here and something
   else arrived. Unions that already accept the value, explicit nulls, missing fields,
   shape mismatches and genuinely wrong values are reported under other error types and
   pass through untouched.
   No second opinion about the schema is kept here to drift from the first, and shapes a
   hand-rolled schema walker would miss -- `dict[str, X]`, tuples, optional unions --
   come free.
2. **The document’s own serializer says how to write it.** The frontmatter is loaded
   round-trip, each offending scalar is replaced by its own source text as a string, and
   the document is written back through the same `new_yaml` serializer everything else
   writes with. The emitter decides quoting; handing it `"1850"` is what makes it write
   `'1850'`.

One direction only: a scalar becomes a string where the contract asks for one, and
nothing else changes.
Round-trip mode preserves comments, key order, quoting style, anchors, line endings and
the notation of every scalar the pass does not touch, so a one-scalar correction is a
one-line diff on a published artifact.
Two notations are not recoverable from the round-trip value and are recorded as tests: a
boolean’s spelling and an integer’s leading `+`.

A related normalization runs at *read* time rather than write time:
`validation.normalize_for_structural_pass` converts dates, decimals and UUIDs to their
serialized form so validation judges the document the schema describes.
That makes a run fair; the conform pass writes the correction to disk so the published
artifact is right for readers who do not run metaproc’s normalizer.

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
  The process execution state is distinct from process definition freshness, and scalar
  code-step errors are projected from durable task state into process status and events.
- **`metaproc status --check <condition>`**: programmatic check mode for agent
  orchestration: asserts completion state via exit codes (0=passed, 1=failures,
  2=still-running), replacing ad-hoc `--dry-run | grep` patterns.
- **`metaproc wait <run-dir>`**: blocks until a run reaches terminal state, then prints
  final status. A terminal process failure returns failure even when no fan-out item
  records exist. Eliminates polling loops in multi-phase playbooks.

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
| `composite` | Resolve `uses`, apply `with`, and recurse in-process under `{run_dir}/{step_id}/`; with `for_each`, create one child scope under `{run_dir}/{step_id}/{item_key}/` |
| `manual` | Wait for `.state/manual-ack.yaml`, then validate outputs and publish completion |

Code step stdout/stderr is captured to `{run_dir}/.logs/{step_id}_{ts}.log`.

### 19.3 Fan-Out Backends

Fan-out steps dispatch through one of two backends:

| Backend | Flag | Mechanism |
| --- | --- | --- |
| `local` | `--backend local` (default) | `RunPool` subprocess pool via `run-parallel` |
| `gcp-worker` | `--backend gcp-worker --cloud` | Submit the orchestrator, which partitions items across N worker VMs via GCP Batch (section 21) |

Local agent fan-out uses RunPool (section 17) with step-scoped `.state/` and `.logs/`
directories. One run execution context owns the optional semaphore shared by fan-out
pools, scalar agent launches, and code work across composite scopes.
For the initial single-profile topology, it also lazily owns one run-scoped RunPool for
scalar agent leaves.
Every scalar leaf reached through mapped child scopes submits a prepared launch to that
pool, so adaptive pressure response, process-tree supervision, status, and events cover
the whole run rather than one child at a time.
Its run-owned executor supervises synchronous handlers, commands, and blocking
credential operations off the event loop.
That executor defaults to 32 workers and grows to an explicit higher
`--max-concurrency`, so executor capacity cannot silently reduce the authored run
ceiling. At terminal cleanup, the context cancels queued executor work and waits for
started work before the orchestrator releases its run lease.
A cancelled executor call is drained before its leaf slot is released; if credential
acquisition returns a late lease, teardown completes first.

Scalar agent launches reuse `LocalBackend` process-group lifecycle through the small
`launch_and_supervise` helper.
Cancellation during launch drains any late handle.
Completion, cancellation, and timeout all close the full process group, escalate
stubborn descendants to `SIGKILL`, and flush a log-filter thread before returning.
Shell-backed code steps apply the same process-group ownership rule inside their sampled
command runner.
A code command owns its descendants only for the duration of the step; it
must not daemonize intentional background work because any remaining group member is
terminated when the command leader exits.
Cleanup failure is logged and does not replace an already-observed exit-zero result.
These paths do not add a second adaptive controller.
The run context supplies the hard leaf ceiling, host admission supplies cross-run
capacity, and the single run-owned RunPool adapts local scalar-agent concurrency.
Command-backed code work retains its existing supervised executor path; moving it into
RunPool requires separate contract evidence.
A second execution profile in the same run is rejected by this first slice.

**Note on backend abstraction:** `local` is a registered `LaunchBackend` implementation
(section 21.8) in the backend registry (`runpool/registry.py`). `gcp-worker` is
different -- it is a multi-VM dispatch mode handled directly in `run-process` via
`dispatch_to_workers()`, not a `LaunchBackend`. It partitions items across N worker VMs,
each of which runs `run-parallel --backend local` internally.
The bare `--backend gcp-worker` form is accepted only inside the Batch orchestrator
container and is rejected on an operator host.
If a second cloud provider were added, a new worker dispatch implementation would
register alongside `gcp-worker` in the `run-process` dispatch logic.

`backend` and `placement` are different concepts.
A backend controls subprocess execution inside one environment; placement controls where
the orchestrator and its worker pool execute.
The future public CLI therefore uses `--orchestrator` and `--worker`, while the internal
`LaunchBackend` registry remains available to each selected environment.
The first placement implementation uses one run-wide worker placement and resource
profile; per-step worker-pool overrides are an additive extension.

### 19.4 CLI Flags

| Flag | Purpose |
| --- | --- |
| `--var KEY=VALUE` | Parameter bindings (repeatable) |
| `--backend` | Fan-out backend: `local`, or `gcp-worker` with `--cloud` outside Batch |
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
| `--max-concurrency` | Local run-wide executable-leaf limit across fan-out pools, scalar steps, and composite scopes; per-worker ceiling for `gcp-worker` |
| `--variant` | Override adapter variant |
| `--adapter-config KEY=VALUE` | Adapter config overrides (repeatable) |
| `--orchestrator-machine-type` | GCP machine type for orchestrator VM (with `--cloud`) |
| `--max-duration` | Max runtime for orchestrator job (e.g., `8h`, `2h30m`, `3600s`) |

These are the implemented flags.
The planned replacement resolves `--orchestrator local|gcp` and `--worker colocated|gcp`
into one immutable execution-topology value before planning or dispatch, then updates
all callers and maintained documentation atomically.
No compatibility alias is required unless a released external consumer that cannot
migrate with Metaproc is identified.

### 19.5 Completion and Resumability

`--force` invalidates a step and all its downstream dependents by renaming the relevant
on-disk `status.yaml` files to `.yaml.stale` (via `_invalidate_downstream()`). This
covers both the standard step directory and any output-derived item directories.
The run-wide force policy descends into composite scopes, so their child tasks are
invalidated rather than immediately reused.
Root `--skip` selectors are not matched against child step IDs.
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

Metaproc runs a process on GCP Batch through
`metaproc run-process <spec> --backend gcp-worker --cloud`, which submits the process
orchestrator and its fan-out workers while preserving the process graph, resume state,
leases, claims, and monitoring contracts described above.
`metaproc gcp run` is a lower-level primitive for one command in one Batch task; it is
not a second process-orchestration API.

The full design — job construction, container bootstrap, orchestrator and worker
entrypoints, cross-host coordination, log retrieval, and secret hydration — is in
[arch-cloud-execution.md](arch-cloud-execution.md), also readable as
`metaproc help arch-cloud`. That document owns cloud execution; this section is
orientation only, so that the two cannot drift apart.

Authentication across the cloud boundary is in
[arch-authentication.md](arch-authentication.md) (`metaproc help arch-auth`), and the
operator procedure is [cloud-dispatch.runbook.md](cloud-dispatch.runbook.md)
(`metaproc help cloud-dispatch`).
