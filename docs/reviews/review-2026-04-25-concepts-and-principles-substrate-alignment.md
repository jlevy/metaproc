# Review: Concepts and Principles Substrate Alignment

Review date: 2026-04-25

Primary document:
[metaproc-concepts-and-principles.md](../../src/metaproc/docs/metaproc-concepts-and-principles.md)

Related context:
[review-2026-04-25-concepts-and-principles-structure.md](review-2026-04-25-concepts-and-principles-structure.md),
[arch-metaproc-core.md](../arch/arch-metaproc-core.md),
[metaproc-design-rev3-proposals.md](../metaproc-design-rev3-proposals.md), the active
[metaproc override + completion evaluator plan](../metaproc-design-rev3-proposals.md),
and the [Earnings Predictions process](../../README.md#usage).

## Executive Summary

The most important change is to make the concepts document describe Metaproc as a
minimal process substrate, not as a committed design for retrospective, meta-circular,
or self-improving workflows.
The current implementation supports process specs, planning, execution, fan-out,
artifact IO, state records, adapter selection, validation, retry, and plugin
registration.
It does not yet have a generic evaluation runtime, gate runtime, experiment
DSL, retrospective DSL, or meta-circular process model.

The prior structure review is mostly directionally right, but several suggestions should
be narrowed before adoption.
In particular, evaluator/gate language, run-record generalization, and retrospective
loop structure should be framed as substrate affordances or future vocabulary, not as
current core semantics.

The active override plan fits this substrate framing well if it is split strictly by
phase. A run-scoped, audited `metaproc override` is a core substrate need because it
records operator judgment without mutating specs or pretending a failed step actually
completed. The optional completion evaluator should remain future-facing: it is a way to
promote repeated override patterns into reviewed process code, not a reason to introduce
a generic evaluation or gate architecture now.

## Top Three Changes

### 1. Add a Substrate Boundary Section

Add an early section that says what Metaproc owns and what it intentionally does not
own.

Metaproc owns:

- Authored process structure: steps, dependencies, modes, variables, IO specs, fan-out
  declarations, retry policy, and adapter configuration.
- Planning: resolved steps, adapters, output roots, dependency references, and fan-out
  inputs.
- Runtime execution: local/cloud dispatch, state files, attempt/result records, logs,
  process events, resume behavior, output validation, and write-boundary checks.
- Extension points: schemas, envelopes, terminal statuses, process rules, compare
  defaults, visualizers, and adapter variants.

Metaproc should not yet own:

- A retrospective-loop DSL.
- A self-improvement or meta-circular process model.
- A generic experiment/sweep/ensemble DSL.
- Domain-specific evaluation semantics.
- Domain-specific promotion rules for earnings prediction forms, knowledge bases, or
  model behavior.

Current code alignment: **aligned**.

The code already looks like a substrate.
Core authored concepts live in [`authored.py`](../../src/metaproc/models/authored.py),
resolved execution state lives in [`plan.py`](../../src/metaproc/models/plan.py),
runtime state records live in [`runtime.py`](../../src/metaproc/models/runtime.py), and
orchestration lives in [`run_process.py`](../../src/metaproc/commands/run_process.py).
The earnings workflow implements its own process shapes in [`process/`](../../examples/)
rather than through a special metacircular primitive.

This is the most important conceptual correction because it prevents the concepts
document from overfitting the framework to one uncertain future use case.

### 2. Separate Execution Selection From Artifact Namespace

Revise the current variant/metaparameter discussion so it does not conflate three
different concerns:

- Which executor, model, tool, prompt, or environment runs a step.
- Where artifacts are written and compared.
- What label users use to discuss a run family or comparison condition.

The document should introduce neutral terms such as:

- `executor profile`: the concrete adapter/model/tool/environment selection.
- `artifact namespace`: the output identity used for directories and comparisons.
- `comparison label`: the human-facing label used in reports or reviews.

Current code alignment: **partly aligned**.

The code already has `ProcessStep.variant` and explicitly documents that it is an
“explicit variant override” which “decouples directory from adapter/model.”
Runtime execution computes an effective variant from CLI override, step variant, or
adapter-derived default.
Adapter selection also supports `config_by_variant`.

However, the CLI-level `--variant` is still used both as an execution selector and as an
artifact/output namespace in practice.
Several earnings process steps pin `variant: claude-cli` to keep execution stable.
That makes the current implementation usable, but the concepts document should avoid
treating the current `variant` word as the final conceptual boundary.

This change is important because earnings prediction needs both repeatable artifact
comparison and flexible executor substitution.
Those should not be accidentally tied together forever.

### 3. Decompose Run Records Without Inventing a Generic Eval Runtime

Keep the run-record idea, but describe it as a family of substrate records rather than
as one universal object that already contains outputs, observations, evaluations, gates,
and annotations.

The document should distinguish:

- Execution records: status, attempts, commands, logs, timings, retries, and adapter
  metadata.
- Output records: declared artifacts, validation status, schema/envelope information,
  and output roots.
- Observation records: measurements, comparisons, usage summaries, or reviews attached
  to artifacts or runs.
- Decision records: verdicts, gates, approvals, promotions, or manual acks.
  A run-scoped override belongs in this category.

Current code alignment: **partly aligned**.

The implementation has status, attempt, result, manual-ack, map-item, and process-event
records. It also has output validation and schema/envelope registration.
It does not have a generic observation/evaluation/decision record model.
Some evaluation-like concepts exist in domain packages and usage models, especially in
earnings prediction and tool-run analysis, but they are not generic Metaproc runtime
primitives.

This framing keeps the substrate open for future evaluation and gating without
pretending those contracts are already settled.

## Other Proposed Changes

### 4. Add Run-Scoped Operator Overrides as Decision Records

Incorporate the override plan into the concepts document as a narrow substrate
primitive:

- An override is run-scoped state, not process configuration.
- It records an operator decision that an upstream step is satisfactory for a downstream
  dependency despite the recorded step outcome.
- It must preserve the original execution record.
  A failed or incomplete step should remain failed or incomplete; the override is a
  separate decision record.
- It must be auditable: actor, timestamp, affected step, optional downstream scope,
  action, and note.
- It should be transient with the run directory unless a later process chooses to turn
  the lesson into a spec change.

Current code alignment: **not implemented yet, but well aligned with the substrate**.

The code already has the right nearby concepts: structured runtime state, manual
acknowledgments, process-status records, resume validation, and dependency completion
checks. A first version can add an `overrides.yaml`-style state file and a
dependency-satisfaction predicate without changing process specs.

One implementation caveat: the active plan describes a “single call site” in the
dependency resolver, but the current code does not have one central resolver.
Resume ancestor validation uses `_verify_ancestors()` and `_is_step_completed()`, while
`_orchestrate()` blocks downstream steps after a failed result.
Phase 1 should centralize “is this dependency satisfied for this downstream step?”
and use it in both places.

The concepts document should present this as the first concrete example of a decision
record. This is more important than introducing a generic evaluator or gate because the
urgent need is operational: when a deadline run has enough usable output, the operator
needs a sanctioned, visible way to unblock downstream summarization or QA.

The implementation plan should stay narrower than its optional future surface:

- Ship step-wide `action: satisfied` first.
- Keep the original failed/completed status intact.
- Prefer current status vocabulary in code-facing docs: Metaproc currently uses
  `completed`, `failed`, and `cached`, not `succeeded`.
- Treat `for_downstream` as useful if the resolver can honor it cleanly.
- Defer or hide `--item` unless item-level dependency satisfaction has clear semantics.
  The current process graph is step-level, so per-item override can imply aggregation
  behavior that does not exist yet.

The future-compatible shape is:

1. Operator judgment becomes an audited override for one run.
2. Repeated overrides become process-level completion policy.
3. Only after multiple policies share a shape should Metaproc consider a generic
   completion/evaluation substrate.

### 5. Treat Evaluators and Gates as Future Vocabulary

The prior review is right that the current document lacks clear language for evaluation
and gating. The adjustment is to avoid presenting those as existing Metaproc components.

Recommended wording:

- “Metaproc currently guarantees output declaration, validation, state recording, retry,
  and dependency flow.”
- “Processes may add domain-specific evaluation steps that read produced artifacts and
  write observation or decision artifacts.”
- “Repeated run-scoped overrides may later justify a reviewed `completion.evaluator`,
  but that is a promotion path, not the default.”
- “A generic evaluator/gate substrate may be added later once repeated process families
  show the same shape.”

Current code alignment: **not aligned as core runtime, aligned as process pattern**.

There is no generic `Evaluator`, `Gate`, `Verdict`, or `Policy` runtime in Metaproc
today. There are QA envelope models and plugin registration hooks, but the earnings
process implements many of its own evaluation, leakage, trust, and review steps.
The concepts document should reflect that current division.

The completion-evaluator plan is compatible with the substrate if it remains a small
process-code hook using the existing `handler:` reference convention.
It should not be described as a generic evaluator/gate runtime in the concepts document
yet.

### 6. Clarify Produced-By Versus Produced-About

Adopt the prior review’s suggestion to promote `produced-by` and `produced-about`, but
keep the concept simple.

- `produced-by`: artifact lineage.
  Which step produced this artifact?
- `produced-about`: observation target.
  Which artifact, step, run, item, or comparison does this measurement or review
  describe?

Current code alignment: **partly aligned**.

`produced_by` already exists in dependency specs and resolved dependencies.
It is validated during plan construction.
There is no first-class `produced_about` field in the generic runtime.
Domain-specific review and evaluation artifacts can already encode this relationship in
their own schema.

The concepts doc should introduce `produced-about` as a recommended substrate
relationship for future generic observation records, not as something already enforced
by Metaproc.

### 7. Fix Composite Process Semantics in the Document

The concepts and design docs should be careful around composite process execution.
The design text currently implies that child processes receive only explicit `with`
variables. The implementation currently starts with the parent variables and then
overlays the explicit `with` values.

Current code alignment: **not fully aligned**.

`_execute_composite_step` in
[`run_process.py`](../../src/metaproc/commands/run_process.py) builds `child_vars` from
all parent variables, then applies `with` overrides.
That is ambient inheritance, not explicit-only parameter passing.

Recommended doc change:

- State the current behavior precisely, or
- Mark explicit-only child inputs as the desired future behavior and add a separate
  implementation task.

For substrate correctness, explicit-only child inputs are likely the cleaner long-term
model because they make composite boundaries inspectable and reduce hidden coupling.
The document should not silently claim that behavior until the code matches it.

### 8. Add Cross-Plane Flow Rules Without Overclaiming Enforcement

The architecture section should include forbidden flows between the process, artifact,
execution, observation, and reflection planes.
This should be written as design intent plus current enforcement status.

Useful rules:

- Runtime execution may write runtime state and declared outputs.
- Domain evaluation steps may read artifacts and write new observation artifacts, but
  should not mutate the artifacts they evaluate.
- Reflection or learning steps may propose changes to source-controlled process assets,
  but those changes should be explicit outputs or reviewable patches.
- Fan-out agent steps must not write directly into shared mutable trees.

Current code alignment: **partly aligned**.

The write-boundary checker enforces important fan-out write constraints.
Planning validates declared dependencies and outputs.
Runtime state IO uses structured state files.
But there is no general “plane” model in the code, and not every forbidden flow is
mechanically enforced.
The document should name the intended constraints while being clear about what is
enforced today.

### 9. Narrow the Artifact Immutability Principle

The document should replace broad statements like “artifacts are immutable” with a more
precise substrate rule:

- Run-scoped artifacts and runtime records should be append-only or reproducible for
  resume and comparison.
- Source-controlled process assets may change through explicit process steps, reviews,
  or commits.
- Shared knowledge bases and promoted outputs are mutable publication targets, so their
  mutation must be explicit and reviewable.

Current code alignment: **partly aligned**.

Runtime state is written through structured helpers, and fan-out write boundaries
prevent a class of unsafe shared writes.
But the earnings process does intentionally publish knowledge-base artifacts and update
process assets, for example in the learn and publish steps.
The concept should support those workflows without pretending all useful artifacts are
immutable forever.

### 10. Move Operating Philosophy Out of Core Principles

The later principles about goals, incentives, roadmaps, and working style are useful,
but they are not substrate invariants.
Move them to an appendix or rename the section to “Operating Philosophy.”

Current code alignment: **documentation only**.

This is not a code alignment issue.
It is a clarity issue.
Keeping substrate principles separate from team operating philosophy will make the
document more useful as an implementation guide.

### 11. Keep Sweep, Ensemble, Experiment, and Retrospective Loops Deferred

The document can mention these as process patterns that the substrate should support,
but it should not promote them into core Metaproc primitives yet.

Current code alignment: **aligned**.

[`metaproc-design-rev3-proposals.md`](../metaproc-design-rev3-proposals.md) already
treats sweep/ensemble/experiment as future work and recommends domain-level
implementation first.
The current code has fan-out, adapter variants, and process composition, which are
enough to experiment with these patterns in authored processes before hardening them
into substrate semantics.

This is especially important for meta-circular workflows.
The earnings prediction process shows the need for retrospective learning, but it does
not prove the general abstraction yet.

## Suggestions From the Prior Review

I agree with these prior-review suggestions, with narrow framing:

- **Swap vocabulary before architecture**, if vocabulary is narrowed to substrate
  concepts.
- **Split architecture planes**, if the planes are described as a mental model and not
  as fully enforced runtime types.
- **Prune and rename principles**, especially to separate substrate invariants from
  operating philosophy.
- **Promote produced-by and produced-about**, with `produced-about` framed as an
  emerging observation relationship.
- **Add operator overrides**, framed as audited run-state decision records.
- **Add forbidden flows**, with explicit enforcement status.
- **Clarify variant definition**, because this is already a source of conceptual
  coupling.
- **Add limits and non-goals**, because this prevents Metaproc from becoming a vague
  universal workflow engine.

I would not yet adopt these suggestions literally:

- **Do not name a generic evaluation/gate component as if it exists.** Use future-facing
  vocabulary and domain process patterns for now.
- **Do not bake loop preconditions into the substrate.** Retrospective loops should
  remain authored process patterns until we have more examples.
- **Do not define map/fan-out alternatives beyond current code.** The current `for_each`
  and discovery model is enough substrate vocabulary for now.
- **Do not turn composite process semantics into documentation fiction.** Either
  describe current ambient inheritance or change the implementation later.

## Code Alignment Table

| Proposed change | Alignment | Notes |
| --- | --- | --- |
| Add substrate boundary and non-goals | Aligned | Current code is a generic process runtime, not a retrospective-loop engine. |
| Separate executor profile, artifact namespace, and comparison label | Partly aligned | Step variants and adapter config exist, but CLI variant still mixes concerns. |
| Decompose run records | Partly aligned | Status, attempt, result, ack, map-item, and event records exist; generic observations and decisions do not. |
| Add run-scoped operator override | Not implemented, aligned with substrate | Fits runtime state and decision-record model; should not mutate original step status. |
| Treat eval/gate as future vocabulary | Not aligned as core | Existing generic validation is narrower than evaluation or policy gating. |
| Promote produced-by / produced-about | Partly aligned | `produced_by` exists; `produced_about` does not. |
| Clarify composite semantics | Not fully aligned | Code inherits parent variables into child processes despite explicit-only design language. |
| Add forbidden cross-plane flows | Partly aligned | Write-boundary and validation cover some constraints, but no general plane enforcement exists. |
| Narrow artifact immutability | Partly aligned | Run records are structured, but source/shared publication workflows intentionally mutate targets. |
| Move operating philosophy out of core principles | Documentation only | No code impact. |
| Defer sweep/ensemble/experiment/meta-circular primitives | Aligned | Current proposals already recommend domain-level experimentation first. |

## Recommended Immediate Edits

The next concepts-document edit should be small and focused:

1. Add an early “Substrate Boundary” section.
2. Rewrite the variant/metaparameter section to separate execution selection, artifact
   namespace, and comparison label.
3. Rewrite the run-record/evaluation discussion to describe current substrate records
   first and future observation/decision records second.
4. Add a focused “Operator Overrides” subsection under decision records.
   Emphasize run-scoped auditability, dependency satisfaction, and preservation of the
   original execution result.
5. Add a short “Current Implementation Alignment” note where the document discusses
   composite processes.
6. Move or rename the broad operating-philosophy principles so they do not read as
   runtime requirements.

These changes preserve the flexibility needed for earnings prediction while keeping
Metaproc small enough to serve other process families.

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
