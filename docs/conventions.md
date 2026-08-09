# Conventions

Framework-level conventions for metaproc projects.
Domain-specific rules belong in each client package’s own `conventions.md`.

## Front-Door Files

| File | Purpose |
| --- | --- |
| `README.md` | Browse-oriented entry point for the package or project |
| `conventions.md` | Local naming and structural rules |
| `changelog.md` | Chronological record of durable changes |
| `TODO.md` | Short local backlog when work is not tracked elsewhere |

Any `*.process.md` file defines a process node; directories group a node with its
sibling artifacts when there are any.
Multiple `*.process.md` files may co-locate in one directory; each is an independent
node. A `main.process.md` is a common (not universal) practice for a package-root
orchestrator spec when one exists, analogous to Python’s `main.py`. Test-only process
specs use a `test-*.process.md` prefix (e.g. under `self-test/` or `tests/fixtures/`),
mirroring the relationship between `tests/` and `test_*.py`.

## File Type Suffixes

| Suffix | Meaning | Example |
| --- | --- | --- |
| `.process.md` | Authoritative process-node definition (typed spec) | `mine.process.md` |
| `.runbook.md` | Agent execution instructions for one step | `predict-item.runbook.md` |
| `.template.md` | Template (`{{ }}` placeholders) filled to produce an artifact; rigor declared by `template.status`. See § Template files and format status. | `prediction.template.md` |
| `.plan.yaml` | Resolved execution plan emitted by `metaproc plan` | `predict.plan.yaml` |
| `.draft.md` | Exploration or work in progress | `overview.draft.md` |

Use dot-separated suffixes such as `name.template.md`, not `name-template.md`.

## File Naming

- **Data and document files** (`.md`, `.yaml`, `.yml`, `.json`, `.txt`, `.jsonl`) use
  **kebab-case**: lowercase, hyphen-separated.
  Examples: `retrieval-kb.yaml`, `kb-index.yaml`, `mine-overview.md`, `qa-report.md`,
  `final-report.md`.
- **Python modules and packages** use **snake_case** per PEP 8. Examples:
  `build_retrieval_kb.py`, `mine_kb_fetch.py`, `arena_helpers.py`.
- **YAML keys and field names** (data *inside* files) use **snake_case**; see §YAML
  field names. Do not confuse these with filename casing.
- **Front-door files** keep their established names (`README.md`, `conventions.md`,
  `changelog.md`, `TODO.md`). Only `README.md` and `TODO.md` are ALL-CAPS; every other
  doc follows kebab-case (see §ALL-CAPS filename rule).
- **Dot-separated suffixes** compose with kebab-case: `retrieval-kb.generated.yaml`,
  `predict-item.runbook.md`, `prediction.template.md`.

## Typed Identifiers

Every self-identifying immutable ID is `prefix-payload`, modeled on AWS resource IDs
(`i-0abc123def456`, `subnet-04ccf456919e69055`).

```text
id      := prefix "-" payload
prefix  := [a-z][a-z0-9]{0,7}          # never contains a dash
payload := readable, dash-separated
```

A prefix carries the type, so an ID is self-describing and a value of the wrong type is
obvious on sight rather than at the point of failure.
Allocate every ID through `metaproc.ids`; never build one with an f-string.

| Form | Example | When |
| --- | --- | --- |
| Compact random | `run-a7x3mq9bk2f0wp` | default |
| Timestamped | `run-20260408T003012Z.2555210000.foayjjhknb` | time-ordered allocation |
| Derived | `rev-3f9a2kx7m1b0c4` | stable child or keyed identity |
| Readable locator | `run-company-info-aapl-2026-07-31` | resumable, human-browsed runs |

### Readable, not parsable

Only the prefix is parsed, by splitting once through `parse_typed_id()`. Dashes inside a
payload are cosmetic word separators, and **no consumer may split a payload on them**.
Reverse-engineering fields out of an ID couples every reader to one producer’s format;
read the run’s own metadata instead.

Where structure genuinely is machine-parsed, the interior uses `.` so the intent is
explicit and cannot be mistaken for a word separator.
The timestamped form is the only current case.
Only helpers in `metaproc.ids`, including `derive_timestamped_typed_child_id()` and
`require_compact_timestamped_typed_id()`, may read that structure.

Readers accept a small set of historical identifier shapes so published partitions stay
resolvable; `metaproc.ids` owns that entirely, and nothing else needs to know about it.
Two identifiers are equal only when their strings are equal, so no lookup, comparison,
or dedup path may rewrite a delimiter to make a match.
Historical underscore IDs remain read-only except when a deterministic derivation must
replay an identity that was already published.
New allocations and derivations use the dash writer form.

### Randomness

`RANDOM_TYPED_ID_BITS` and `TIMESTAMPED_TYPED_ID_RANDOM_BITS` are the defaults; both
allocators take a `bits` parameter so a caller can widen or narrow a namespace without a
new API. Non-temporal allocations allow 64 through 256 requested bits.
At one million allocations, the birthday-bound approximation `n(n-1)/(2*2^b)` is about
`2.7e-8` at the 64-bit minimum and `1.1e-10` at the 72-bit default.
Timestamped allocations allow 48 through 256 random bits because collisions compete only
within the same timestamp and fractional-second bucket; even 10,000 allocations in one
bucket have an approximate `1.8e-7` collision probability at 48 bits.

Deterministic non-temporal payloads allow 12 through 50 base36 characters.
Twelve characters provide about 62 bits and an approximate `1.1e-7` birthday bound at
one million identities; the 14-character default provides about 72 bits.
Timestamped child IDs allow 10 through 50 characters because the parent timestamp and
immutable parent ID also scope the derived namespace.
These bounds keep custom widths compatible with compact classification and recursive
timestamp-child derivation.

## Process Structure

- `process/` holds authored methodology and templates.
- `runs/` holds emitted runtime artifacts.
- The framework does not prescribe one domain-specific run directory layout.
  The process spec’s declared outputs are the contract.
- Fan-out source artifacts are explicit in `for_each.over`; do not rely on an implied
  dated folder layout.
- The YAML frontmatter in a `.process.md` file is the machine-readable spec.
  The markdown body is human context: scope, responsibilities, and operating notes.

## Harness-Owned Runtime Artifacts

The harness, not agents, owns runtime state and logs.
A run directory has exactly three top-level branches:

- `<run_dir>/.state/`: durable engine bookkeeping needed for resume.
  Files here are machine-internal records and do not use the frontmatter envelope
  convention. Agents must not hand-edit them.
- `<run_dir>/.logs/`: operational source streams, workflow tool streams, captured
  process output, and derived JSONL outputs.
  Logs are operational artifacts and are normally gitignored.
- `<run_dir>/<artifact-tree>/`: whatever the spec’s output templates produce.
  No engine bookkeeping is written here.

The `.state/` and `.logs/` branches further organize per-step and per-task files into
stable sub-namespaces.
Item-keyed task state is addressed by the explicit `for_each.key` template; it is not
inferred from the artifact path.
In runtime paths, `steps/` means step-runner control-plane scope and `tasks/` means task
execution scope.

Runtime terminology:

- **Item:** workflow data record or scalar supplied to a step, often from an items file
- **Task:** harness-owned execution record for one step applied to one item; scalar
  steps have one task for the step
- **Attempt:** one launch or retry within a task
- **Step runner:** harness control plane that executes a step, especially a fan-out pool

Do not use `task` as a synonym for item.
The `tasks/` path segment names the runtime record that holds status, attempts, results,
and per-attempt logs.

Non-fan-out steps write per-task state directly under `<run>/.state/tasks/<step_id>/`
(no `<item_key>` sub-level, since there is exactly one task).
Composite child processes get the same three-branch shape recursively at
`<run>/<composite_step_id>/`.

Large completed log files may be compacted and stored as `.jsonl.gz` or `.log.gz`. The
logical file type remains `.jsonl` or `.log`; use metaproc commands and metabrowser
instead of hand-written file readers so gzip passthrough works consistently.

Agents should not edit these files manually.

For the full list of artifacts (filename, path, format, schema, lifecycle, writer,
readers), see [artifact-catalog.md](artifact-catalog.md).
For the operator-facing view (what to look at while watching or reading a run), see
[metaproc-operator-reference.md §Runtime Layout](../src/metaproc/docs/metaproc-operator-reference.md#runtime-layout).

## Project-Level Docs

Framework-owned `.state/` and `.logs/` artifacts are runtime; this section covers the
human-authored operational documents that sit above runtime state: plan specs, evidence,
phase reports, and status ledgers.

| Location | Purpose | Examples |
| --- | --- | --- |
| `docs/project/specs/active/` | Active plan specs | `plan-YYYY-MM-DD-<slug>.md` |
| `docs/project/specs/done/` | Completed plan specs retained as implementation history | `plan-YYYY-MM-DD-<slug>.md` |
| `docs/project/specs/future/` | Deferred or future-only plan specs | `plan-YYYY-MM-DD-<slug>.md` |
| `docs/project/specs/active/evidence/<slug>-YYYY-MM-DD/` | Dated evidence subdirs that back a plan spec | `README.md`, `usage-snapshot-*.md`, tabular `.txt` artifacts |
| `docs/project/specs/active/evidence/<slug>/phase-<N>-*/` | Per-phase evidence subdirs under a long-running epic | `README.md`, dispatch logs, per-worker terminal output |
| Phase reports | Prose report at the top of a phase’s evidence subdir, summarising what happened | `README.md` |
| Final reports | Rolled-up report across phases for a whole epic | `final-report.md` (lowercase; see ALL-CAPS rule below) |
| Status ledgers | Living tables of per-phase state that predate or accompany a final report | `status-ledger.md` (new convention; older dirs carry legacy `STATUS.md` pending migration) |
| Evidence artifacts | `.md` with YAML frontmatter. Frontmatter is the schema-defined source of truth; the body renders the same data as Markdown tables and narrative context. See §Evidence artifact format. | `scoreboard.md`, `per-field-cand-win.md`, rubric scorecards |

### ALL-CAPS filename rule

`README.md` is the only filename that uses ALL CAPS as a convention.
Every other project doc uses lowercase-with-hyphens: `final-report.md`,
`overnight-plan.md`, `continuity-notes.md`. New evidence files follow the lowercase rule
on creation.

### Dated-slug convention

Plan specs and evidence subdirs carry a `YYYY-MM-DD` stamp in the name:
`plan-2026-04-20-<slug>.md`, `evidence/<slug>-2026-04-20/`. The date is the creation
date, not the close-out date.
It is stable across the spec’s lifetime and makes chronological listing trivial.

### Evidence artifact format

Evidence and reporting artifacts, including scoreboards, per-field breakdowns, rubric
scorecards, and anything a plan spec publishes under `evidence/`, use `.md` files with a
YAML frontmatter envelope.
The frontmatter is the schema-defined source of truth; the markdown body renders the
same data as one or more human-readable tables, with optional narrative context.

- The envelope follows the §Frontmatter Document Model and §Schema tokens conventions
  (`<module>:<ClassName>/<version>`).
- Frontmatter is authoritative and tool-consumable; the body must not introduce data
  that isn’t in the frontmatter.
  When the two could drift, regenerate the body from the frontmatter.
- One artifact = one file.
  Do not emit parallel `.txt` and `.yaml` pairs or `*_detail.yaml` siblings for the same
  data.
- Streaming or append-only logs (`*.jsonl`) and runtime `.state/*.yaml` are exempt; they
  are machine-internal, not authored evidence.
- `README.md` in an evidence directory stays prose-first.
  It may carry frontmatter for its own metadata (`title`, `status`, `parent_epic`) but
  the body is narrative; it is an index/takeaways doc, not a tabular artifact.

Migration: existing `.txt` evidence artifacts and `*_detail.yaml` siblings are
grandfathered. Migrate opportunistically.
When rewriting the artifact, regenerating from a new dispatch, or closing out the parent
phase. New emitters must follow this convention on creation.

## File Format Policy

The choice between YAML, JSON, JSONL, and softschema markdown is driven by the
artifact’s role and size, not by per-artifact preference.
The rules below are normative; new artifacts must follow them.
For the complete list of artifacts Metaproc emits and the format each one uses, see
[`artifact-catalog.md`](artifact-catalog.md).

### Selection rules

| Role | Format | Why |
| --- | --- | --- |
| Streams and many-record append-only files | **JSONL** (always) | Line-recoverable, streamable in chunks, typed via Pydantic discriminated unions. Gzipped (`.jsonl.gz`) when archived. |
| Small to moderate machine-readable files | **YAML** (strongly recommended) | State, snapshots, configs. Readable from a terminal, comment-friendly, typed via Pydantic models. |
| Large, deeply-nested, or complex machine-readable documents | **JSON** | YAML indentation becomes unreadable at depth and slower to parse; JSON wins for tree-shaped or perf-bound machine documents. |
| Human-readable documents bundling structured and unstructured content | **Softschema MD** (YAML frontmatter with a Markdown body) | One file carries both representations; frontmatter is parsed by tooling, and the body is prose. See §Frontmatter Document Model. |
| Externally-owned payloads (cached upstream responses) | Whatever the upstream produced | Round-trip fidelity and parse speed. Typically JSON. |
| Raw stream captures (stderr, plain dumps) | `.txt` / `.log` | No structure to enforce. |

### Additional rules

- **Typing is non-negotiable.** Every YAML, JSON, JSONL, or softschema artifact has a
  paired Pydantic model.
  Bare-dict serialization is not the convention; new artifacts that serialize ad-hoc
  dicts are bugs to clean up, not exemplars to copy.
- **Format follows role, not size.** A 50 KB `process-status.yaml` stays YAML because
  it’s state; a 200-byte invocation sidecar stays YAML for the same reason.
  A deeply-nested tree is JSON regardless of byte size.
- **Streams are always JSONL.** Any append-only file that grows with records uses JSONL.
  Do not replace a stream with periodic JSON snapshots.
- **One file per artifact.** Do not emit `*.yaml` and `*.json` siblings for the same
  data. Pick the format the role requires and stick to it.
- **Sidecars are YAML by default.** The exception is when the sidecar is too large for
  readable YAML or holds an externally-owned payload, in which case JSON applies.

### Why YAML for small-to-moderate machine docs

Operators inspect state files with `cat`, `less`, and `grep` during debugging.
YAML survives those tools intact (no escaped strings, no flat one-liners), supports
comments where the schema permits, and round-trips cleanly through the
`frontmatter-format` library when an envelope is involved.
JSON wins only when depth or size make YAML harder to read than easier.

## Frontmatter Document Model

### One file with structured and unstructured content

Every artifact that mixes structured fields and human prose is a single Markdown file
with YAML frontmatter.
Structured data lives in frontmatter; prose lives in the body, beside the rationale.
There is no separate `*.yaml` sidecar mirroring small structured fields from the same
artifact. Splitting them severs each value from its context and forces consumers to
choose which is authoritative.

The boundary is **demand-driven**: a field belongs in frontmatter exactly when
downstream code (or a softschema check) parses it.
Prose-only narrative that no consumer reads stays in the body.
The moment a field gets parsed, lift it.

Use sidecars when the structured payload is too large for frontmatter, is
high-cardinality, or needs to be read or validated outside the Markdown context packet.
The sidecar format follows §File Format Policy: YAML for small-to-moderate machine
state, JSONL for streams, JSON for large or deeply-nested machine documents and
externally-owned payloads.

Pure-YAML files (no body) are appropriate when there’s no human prose to bundle:
machine-emitted runtime state (`runpool-status.yaml`, `dispatch-manifest.yaml`) or
schema sidecars produced by `softschema.compile_model`.

See [AGENTS.md](../AGENTS.md) and the
[File Format Policy](conventions.md#file-format-policy) for the full rule and examples.

### Envelope convention

Every frontmatter document uses a **self-identifying top-level envelope key**. The first
(and only) top-level key names the document type; the value is a mapping containing the
payload fields.

```yaml
---
usage:
  schema: "metaproc:UsageReport/0.2"
  run_id: run-20260410T090000Z.1180400000.mq3xk7vb2p
  # ... payload fields ...
---
```

Standard envelope keys: `plan`, `process`, `progress`, `qa`, `qa_summary`, `usage`.
Domain packages register additional keys (e.g. `prediction`, `retro`, `confidence`,
`record`).

Runtime `.state/` files (`status.yaml`, `attempt.yaml`, `result.yaml`,
`manual-ack.yaml`, `process-status.yaml`, `run-config.yaml`, and related harness-owned
state) are machine-internal records, not authored documents; they do not use the
envelope convention.

### Pydantic model conventions

Each frontmatter document type has a paired Pydantic model:

| Layer | Naming | Example |
| --- | --- | --- |
| Inner model | Domain noun, PascalCase | `UsageReport`, `Prediction`, `Retrospective` |
| Envelope | `<Inner>Envelope` | `UsageEnvelope`, `PredictionEnvelope` |
| Field for envelope key | matches the YAML key | `usage: UsageReport` |

Rules:

- Name types for the domain concept, not the serialization format (e.g. `Prediction`,
  not `PredictionYaml`).
- Use `Literal` types for controlled vocabularies (e.g.
  `Literal["beat", "miss", "inline"]`).
- Use concrete Pydantic models or `TypedDict` for nested structures, not
  `dict[str, Any]`, when the shape is known.
- Do not set `extra = "allow"` unless the model must pass through unknown fields (e.g.
  generic form templates).
- Envelope models are registered in `ENVELOPE_MAP` (`metaproc.io.frontmatter`) for
  auto-detection by `load_frontmatter_typed`.
- The `schema_` field (see §Schema tokens) lives on the **inner** model only, never on
  the envelope. The inner class’s PascalCase name and the `<ClassName>` portion of its
  schema token are the same identifier; an envelope class with a `schema_` field is a
  bug (the token carries the inner name but the Pydantic class has an `Envelope` suffix,
  so the two drift apart).

### Schema tokens

Schema tokens are compact self-identifying strings embedded in frontmatter to declare
which model version a document conforms to.

Format: `<module>:<ClassName>/<version>`

Every inner model must have a `schema_` field (aliased to `schema` in YAML) whose
default is a valid schema token.
The `schema` field is the sole version identifier; there is no separate `schema_version`
field.

Examples:

- `metaproc:ProcessSpec/0.1`
- `metaproc:UsageReport/0.2`
- `metaproc:Plan/0.4`
- `example_plugin:ScoreboardV2/0.2`
- `example_plugin:JudgeVerdictsV2/0.2`
- `example_plugin:RecordDocument/0.1`

Components:

| Part | Rule |
| --- | --- |
| `module` | Broad package name (`metaproc`, `example_plugin`), not a nested sub-module |
| `ClassName` | PascalCase, matches the **inner** Pydantic model’s class name exactly. See §Pydantic model conventions; this is not the envelope class. |
| `version` | Semver-ish, opaque to the framework (e.g. `0.1`, `0.2beta`) |

The `schema` field carries the token.
Domain-specific version fields like `form_version` or `retro_schema_version` describe
content format versions, not the envelope contract.
They are separate concerns.

Utilities: `parse_schema_token` and `format_schema_token` in `metaproc.io.schema_token`.

### Schema registry

The framework builds a schema registry from `ENVELOPE_MAP` and explicitly registered
standalone artifact models by extracting each model’s field aliased to `schema`. Use
`resolve_schema(token)` from `metaproc.io.schema_token` to look up the Pydantic model
class for any token.
Standalone artifacts such as `resources.json` resolve without a frontmatter envelope.

Domain plugins can also register additional contracts via the plugin registry
(`registry.register_softschema()`).

## Versioning

- Version identifiers are opaque to the framework.
- The default project convention is `v` plus an integer: `v0`, `v1`, `v2`.
- Version directories usually live under `process/<node>/vN/`.
- The active version is selected at invocation time through a declared process input
  such as `form_version` backed by `param: FORM_VERSION`. Set `default: "vN"` on the
  input to lock in the current release; override via CLI/env to pin an older version.
- Older `vN/` directories stay on disk as historical artifacts; no loader code indexes
  them.

## Discriminator Fields

The spec-level vocabulary for “what subtype of this class is this instance?”
is three words, each scoped to one axis:

| Field | Scope | Example values |
| --- | --- | --- |
| `mode` | `ProcessStep`: how does this step execute? | `agent`, `code`, `composite`, `manual` |
| `kind` | Polymorphic discriminator on many classes: what subtype of *this* class is this? | `ValueType.kind`: `string \| path \| list \| map`. `IOSpec.kind`: `file \| directory \| stream`. `VizNode.kind`: `step \| dep \| process \| file`. `VizEdge.kind`: `needs \| produces \| consumes`. `EnvMeta.kind`: `REAL \| TUNABLE \| SECRET \| OPTIONAL`. `LogEvent.kind`: agent-log event types. |
| `format` | Wire format / schema for a file | `frontmatter-md`, `yaml`, `json`, `jsonl` |

`kind` is always scoped by its owning class; the class context disambiguates values that
might look similar. A reader should never need to guess which `kind` is meant once they
know which object they’re inspecting.
The values differ per class by design.

`ProcessDep` has no `role` axis.
Format, lifecycle, and consumer are each answered by existing signals (filename suffix,
path prefix, or a step reference like `uses:` / `for_each.over:` / `prompt_paths`), so a
closed role enum would re-encode three separate questions in one tag.
See `arch-metaproc-core.md` §11.6 for the full rationale.

## YAML and Template Rules

### YAML field names

- Schema keys use `snake_case`.
- Domain-authored logical keys under process `inputs:`, process `deps:`, step `inputs:`,
  step `outputs:`, and fan-out bindings are bare lowercase identifiers.
- Template resolution is case-sensitive.
  The harness does not uppercase or lowercase authored names.

### Template namespaces

- Template expressions use double curly braces, such as `{{run.id}}` or `{{item}}`.
- Framework-owned names live only under reserved dotted namespaces: `run.*` and
  `step.*`.
- Domain-authored names stay bare lowercase with no prefix: process params, composite
  `with:` bindings, and fan-out item fields.
- Unknown dotted prefixes are errors.
  Unknown members under a reserved prefix are also errors.
- The resolver operates on explicit runtime context only.
  If a value originates from the environment, the harness imports it into context before
  resolution; the authored surface does not rely on resolver-level env fallbacks.

### Framework built-ins

The framework-owned template surface is closed and intentionally small:

| Variable | Meaning |
| --- | --- |
| `{{run.id}}` | Framework-managed run identifier |
| `{{run.dir}}` | Output root for the current run |
| `{{run.parent_dir}}` | Parent directory that contains all runs |
| `{{run.execution_profile}}` | Named adapter runtime profile selected for this run |
| `{{run.artifact_namespace}}` | Output grouping label for this run |
| `{{run.variant}}` | Deprecated migration alias for `{{run.artifact_namespace}}` |
| `{{step.prompt_path}}` | Resolved path of the current prompt file |
| `{{step.prompt_paths}}` | List-valued prompt-file set for the current step |
| `{{step.outputs_list}}` | Comma-joined resolved output paths for the current step |

Names such as `date`, `run_mode`, `item`, `category`, and `event_date` are not framework
built-ins.
They are ordinary domain bindings and should be declared explicitly where they
enter scope.

### Template files and format status

A template is a document with `{{ }}` placeholders that is filled to produce an
artifact. Every template file, strict or loose, uses the single `*.template.md` suffix;
there is no separate strict suffix and no template engine such as Jinja.
Template expressions reuse the placeholder syntax above (`{{var}}`, domain names bare
lowercase, framework names under `run.*`/`step.*`), and document templates are rendered
with that same `{{ }}` substitution, never Python `str.format` or hand-built strings.

A template’s frontmatter declares how rigorously its variables are defined, via a
`template.status` field.
The values form a migration ladder, so an existing form can be tightened over time
without renaming the file:

| status | Meaning | Filled by |
| --- | --- | --- |
| `unstructured` | No template variables; a blank form or scaffold with prose or blanks. | agent, during execution |
| `loose` | Uses `{{ }}` variables, but not all are declared in frontmatter and they are not validated. | agent or code; partial fills allowed |
| `validated` | Frontmatter declares every variable (`template.vars`), and that set exactly matches the `{{ }}` placeholders in the body. Safe for strict, deterministic code rendering; rendering errors on any missing or unknown variable. | code (a renderer); all variables required |

```yaml
template:
  status: validated
  vars: [date, slug, item_table]   # required when status is `validated`
```

A `validated` template may be enforced by a softschema binding so its declared
`template.vars` and the body placeholders cannot drift.
New code-rendered templates should be `validated`; pre-existing agent-filled forms are
`unstructured` or `loose` until promoted.

### Path and file suffixes

- Path-valued fields and variables end with `_path`.
- Directory-valued fields and variables end with `_dir`.
- File-valued fields and variables that point to one concrete file may use `_file`.
- Prefer semantic names over generic `path` suffix chains.
  For example: `prompt_path`, `packet_dir`, `packet_file`.
- Output and input paths in authored specs should be explicit and process-local.

### Prompt fields and envelope

- Agent steps use `prompt_prefix: str | None` for short inline bindings.
- Agent steps use `prompt_paths: list[str]` for prompt files whose contents are inlined
  into the composed prompt.
- Long procedure text belongs in prompt files, not in `prompt_prefix`.
- Runtime prompt-file inlining uses `<prompt-file path="..."> ... </prompt-file>`. The
  legacy `<runbook>` envelope is retired.

### Collision rule

- Domain params and fan-out item fields share the same bare identifier namespace.
- A name that would appear in both process scope and item scope in the same resolution
  stack is invalid and must be renamed before execution.
- Avoid case-only distinctions such as `item` and `ITEM` in the same contract.

## CLI Documentation Rules

When documenting CLI usage:

- pass runtime variables with repeated `--var KEY=VALUE`
- pass adapter overrides with `--adapter`
- pass adapter settings with repeated `--adapter-config KEY=VALUE`
- prefer non-interactive commands in examples

## Logging Rules

### Policy: every log path lives on persistent storage, never `/tmp/`

**All framework-owned, workflow-owned, and operator-owned logs (wrapper logs,
supervision pulse outputs, and ad-hoc diagnostics) must be written under the runs
directory tree.** `/tmp/`, `/var/folders/.../T/`, and any other system-managed temporary
location are session-only on most platforms (macOS clears `/tmp` on reboot; Linux
systemd-tmpfiles clears it on a schedule; some sandboxes wipe it on process exit) and
defeat unattended-execution recovery.
An overnight batch that survives a sleep, crash, or reboot must be able to reconstruct
what happened, which requires the log to still exist.

This rule applies to every log artifact, regardless of who writes it:

- **Per-run logs:** `<run_dir>/.logs/...` (framework/workflow).
  See path layouts below.
- **Wrapper/launcher logs** (operator stdout/stderr from `dispatch_control.py start` /
  `metaproc run-process` / `metabrowser` / etc.)
  and any other cross-run operator artifact: write under `<RUNS_DIR_ROOT>/.logs/` (the
  `.logs/` directory at the workflow’s runs-root level, alongside the per-run dirs).
  The filename should encode the batch slug, for example,
  `<RUNS_DIR_ROOT>/.logs/wrapper-<batch-slug>.log`. The `**/.logs/` gitignore pattern
  covers these so they persist on disk but aren’t accidentally committed.
- **Supervision PULSE / status snapshot dumps** that an agent wants to read later: same
  `<RUNS_DIR_ROOT>/.logs/` location, suffixed by what they capture (e.g.
  `pulse-<batch-slug>-<iso>.txt`).
- **One-shot install/setup downloads** (gcloud tarballs, service-account JSON during
  bootstrap, etc.) MAY use `/tmp/` because they are sourced fresh on every run anyway.
  These are NOT logs. Anything that an agent or operator might need to look at *after* a
  crash or reboot is a log and must live under the runs tree.

If a batch spans multiple workflow run-roots or has no obvious runs-root anchor (true
cross-workflow tooling), choose the most-specific anchor and create the parallel
`.logs/` directory there.
The principle is: any path an agent might need to re-read across a process boundary
lives where the runs live, not in `/tmp/`.

### Path layouts

- framework-owned and workflow-owned logs live under the run-scoped `.logs/` directory
  computed from the process outputs
- run-level logs include orchestrator events (`process-events.jsonl`) and
  adapter/session logs written by the harness
- runpool logs are scoped by the step runner or worker that writes them:
  `.logs/runpool/steps/<step_id>/events.jsonl` or
  `.logs/runpool/workers/<worker-id>/events.jsonl`
- workflow-owned tool logs live under `.logs/tools/<tool-name>/`
- derived JSONL outputs live under `.logs/derived/`; extractors must not treat
  `.logs/derived/` as a source tree
- operator wrapper / supervision logs live under `<RUNS_DIR_ROOT>/.logs/` with a
  batch-slug filename (see policy above)
- log filenames should preserve enough context to identify step, item key, and time
- logs are operational artifacts and should normally be gitignored
- operator-facing command guidance lives in
  [metaproc-operator-reference.md](../src/metaproc/docs/metaproc-operator-reference.md)

## Observability

Three artifacts carry tool-use observability for every workflow run.
Full contract in [`arch-metaproc-core.md §14.7`](arch/arch-metaproc-core.md).

| Artifact | Scope | Owner | Role |
| --- | --- | --- | --- |
| `<item>/.logs/tools/<tool-name>/invocations.jsonl` | Per workflow item | workflow plugin | Optional config record plus one line per tool invocation |
| `<variant>/.logs/*.jsonl` | Per pi-cli session | pi-cli | `tool_execution_start` / `tool_execution_end` / `rate_limit_event` records |
| `<run>/usage.md` frontmatter `tool_profiles` block | Per run | metaproc `write_usage_report` | Aggregated per-variant `ToolRunProfile`, including cutoff-discipline and native web-search signals |

The `tool_profiles` block is the single operator-facing rollup: everything else in the
three sources is raw event data the rollup consumes.

Partial-closure signal for runbook gap A (native web-search activity) rides on the
`native_web_search` config-stub flag.
This presence-only signal is aggregated into `ToolRunProfile.native_web_search_configs`.
*Native web search* is the provider-neutral term (Vertex grounding, Anthropic
`web_search_*`, OpenAI `web_search_preview`); reserve *grounding* for Vertex-specific
references. Per-turn activity is tracked as a known open gap in the
[tool-use-observability runbook](arch/arch-metaproc-core.md#147-tool-use-observability);
closing it fully requires per-provider upstream changes outside metaproc scope.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
