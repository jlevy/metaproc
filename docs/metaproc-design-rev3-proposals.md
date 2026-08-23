# Metaproc Design Rev3 Proposals

Remaining rev3 candidates on top of `arch-metaproc-core.md` (rev2i).

This document intentionally omits proposals that are already implemented on the current
branch and have been folded into the main design docs.
The scheduling portions of P1 and P3 are superseded by
[execution-model-design.md](execution-model-design.md), which owns the task-level
scheduling contracts.
That includes:

- promotion of stable production subsystems into the main design
- explicit harness ownership of preflight/auth-token concerns
- `needs` propagation and graph validation
- the current log/state split and run-scoped `.logs/` model
- GCP cloud execution and provider-specific CLI naming
- `auth-check` in the CLI surface
- removal of the vestigial `gcp-batch` `LaunchBackend`
- the operational fix of moving mine roster generation into the in-DAG `setup-roster`
  step
- tool-use operational observability (per-call telemetry, failure-kind taxonomy,
  `cutoff_disc_pct`, partial native web-search closure via `native_web_search_configs`)
  landed in `arch-metaproc-core.md §14.7` under the original design

## Categories

| Category | Meaning |
| --- | --- |
| **execution** | New commands or runtime behavior in the harness/orchestrator |
| **authored** | Changes to the authored `*.process.md` surface |
| **runtime** | Changes to emitted runtime/state layout |
| **future** | Designed but intentionally deferred work |

## P1. `reduce` CLI Command

**Category:** execution **Modifies:** sections 8.3, 11.4, 20 in `arch-metaproc-core.md`

`needs` is now propagated and enforced by `run-process`, but there is still no command
that says “start from this completed map step and run its downstream aggregations.”

Proposal:

- Add `metaproc reduce <process_dir> --step <map-step>` as a convenience command.
- Compute the downstream subgraph from `needs`.
- Run only those reduce/aggregation steps, skipping already-completed outputs.
- Preserve the same resume, validation, and blocking behavior as `run-process`.

Why it still matters:

- Operators still rerun downstream steps manually (`--only`, `--from`, individual
  `run-step` invocations) after a large fan-out finishes.
- The dependency graph is already rich enough to support this; the missing piece is the
  operator-facing command surface.

## P2. Capabilities / Policy / Adapter-Config Separation

**Category:** authored, future **Adds:** a new authored policy layer between process
steps and adapter-specific config

Current process specs still embed adapter-vendor tool names and permissions directly in
adapter config.
That works for today’s small adapter set, but it couples authored process
specs to the details of each runtime.

Proposal:

- Add an adapter-agnostic capability surface (`fs.read`, `fs.write`, `shell`,
  `web.search`, `git.status`, etc.).
- Add a policy block for durable operator constraints (`writable_roots`, `allow_push`,
  network policy, and similar controls).
- Keep adapter config focused on runtime-specific invocation details (model, timeout,
  provider, output format, session settings).

Why it is still deferred:

- The current adapters are close enough in shape that direct config remains workable.
- The write-boundary work already covers the highest-risk file-mutation problem.
- A capability/policy layer becomes much more valuable once multiple runtimes need the
  same authored process to travel unchanged.

## P3. Prepare / Map-Items Fan-Out Model

**Category:** authored, future **Relates to:** section 6.7 (`for_each`)

The current `for_each` model is production-worthy and now cleaner than it was earlier in
rev2. But the harness still owns roster parsing and item-context extraction.

Proposal:

- Promote the prepare/scatter/map/reduce pattern from a design sketch to a first-class
  authored option.
- Move domain-specific item parsing and enrichment into an explicit code step that
  writes plain work-item descriptors.
- Let the harness consume only those generic descriptors for map execution.

Why it remains future work:

- The plugin-based roster parsing is working well enough today.
- Migration would touch every fan-out process spec and would add an extra authored step
  to the common case.

## P4. Workspace / Mutation Workflows Promotion

**Category:** authored, future **Relates to:** section 16 (Optional workspace/state
surface)

Metaproc’s mainline use today is still file-artifact production, not code mutation
inside isolated workspaces.
The design doc keeps workspace semantics as an optional future surface.

Proposal:

- Promote workspace semantics from “optional/future” to a named extension point with a
  stable authored shape.
- Make workspace isolation, writable roots, and commit policy explicit for mutation and
  evaluation loops.

Why it remains future work:

- The current analysis workflows do not need first-class worktree/workspace
  orchestration.
- The write-boundary work solved the immediate safety problem without requiring a new
  workspace runtime.

## P5. Subagent Adapter (In-Process Step Execution)

**Category:** execution, future **Relates to:** section 12 (Adapter contract)

All current adapters are subprocess adapters.
That preserves strong harness boundaries, but it can be heavyweight for very small
aggregation or validation steps.

Proposal:

- Add a `subagent`-style adapter that executes a step inside the parent coding-agent
  session rather than spawning an external CLI process.
- Keep success defined by declared artifacts plus harness validation, not conversational
  state.

Open questions:

- How much lifecycle parity with subprocess adapters is required?
- How do logs, retries, and output publication map onto an in-process adapter?
- Does this blur the current harness/agent boundary too much to be worth the lower
  overhead?

## P6. Declarative Run Source

**Category:** authored, future **Relates to:** sections 6, 11 in `arch-metaproc-core.md`

The operational problem from early rev2 is mostly gone: production mine runs no longer
require a manual roster-prep command because `setup-roster` is now an ordinary in-DAG
code step.
What remains open is whether that pattern should become a first-class authored
surface.

Proposal:

- Add a declarative `source:` section (or equivalent first-class init surface) so the
  harness can materialize a roster from a dataset/config input without requiring each
  process to hand-author a `setup-roster` step.
- Keep the transformation domain-owned, either through a plugin hook or a process-owned
  handler reference.

Why it is still future work:

- The explicit `setup-roster` step is currently clear, testable, and resume-safe.
- A first-class `source:` shape should only be added if the pattern repeats enough to
  justify new authored surface area.

## P7. Sweep / Ensemble / Experiment Primitives

**Category:** authored, future **Relates to:** the Type A and Type C optimization loops
in `metaproc-concepts-and-principles.md` §5

The concepts doc names three composition primitives over runs — sweep, ensemble,
experiment — that mechanize the Type A and Type C optimization loops.
None are first-class harness primitives today.
The pattern exists in domain code (see “Grounding example” below); the proposal is to
promote it to the framework once it has recurred enough across domains to earn the
abstraction.

### Vocabulary

These three definitions live here rather than in the concepts doc because the harness
does not yet implement them.
Promoting them to the concepts doc is part of this proposal landing.

- **Sweep:** a set of runs of the same step that share inputs and most of run context,
  varying along one or more sweep axes drawn from metaparameters.
  Variation is structured: a grid, a random sample, or a named list.
  A sweep is the unit the Type A loop operates on.
- **Ensemble:** a set of runs that share *all* metaparameter values (no axis variation),
  replicated to reduce stochastic variance.
  Has an **aggregator** (majority vote, mean, top-k consensus) that combines member
  outputs into a single ensemble output.
  Ensemble size and aggregator are themselves metaparameters.
  Composes with sweeps: an `M × N` experiment runs an ensemble of size `N` at each of
  `M` grid points.
- **Experiment:** a named, persistent entity that composes one or more sweeps and/or
  ensembles, a fan-out item set, step versions and reuse keys, and declared **analyses**
  over the resulting runs (cross-tabs, validation comparisons, metric aggregations)
  producing output tables.
  An experiment is to a sweep/ensemble roughly what a process is to a step: a
  composition with its own declared shape.

### Design constraints

These two design constraints are load-bearing for the primitives once they exist:

- **Sweep is the unit of Type A.** The first-class shape of “iterate on one step” is a
  sweep of runs sharing upstream and run context, varying on declared metaparameter
  axes. Anything below that (one-off reruns) is a debugging convenience, not a research
  primitive.
- **Experiment surface is defined like a dataset.** Sweep axes, item sets, output
  tables, cross-tabs, and validation metrics are declared up front — the same discipline
  a data scientist brings to an experiment.

### Grounding example: example_plugin experiment process

A downstream `example_plugin/process/experiment/` tree is a working domain
implementation of this pattern, manually orchestrated:

- The **manifest** (`experiments/{name}/manifest.md`) is the experiment entity —
  hypothesis, arms, run IDs, dates, metrics, conclusion.
  A downstream `experiment.template.md` defines the manifest shape.
- **Arms** are sweep variants.
  Each arm has a `variant` metaparameter (model or prompt version) and a list of
  `run_ids` produced by separate `run-process` invocations.
- **Tiers** (dev / validation / production) are sweep cohorts at different scales and
  sample sizes, each defined by its own downstream process spec.
- **Measurement** comes from running the domain’s evaluation process against each run
  and folding its accuracy and cost metrics back into the manifest.

What promoting this to a framework primitive would mean:

- A declared `*.experiment.md` shape that the harness understands.
- Harness-managed sweep dispatch — one CLI invocation launches all arms.
- Reuse-key awareness so re-running an arm with identical metaparameters short-circuits.
- Aggregation primitives that emit the declared output tables.

Why it remains future work:

- Today’s `for_each` is fan-out over items, not a sweep over knobs; the operator
  manually invokes `run-process` once per arm.
- The example_plugin experiment process is the only domain implementation; the pattern
  needs to recur in at least one more domain before promoting to typed infrastructure
  (codification follows experiment).
- The Type A and Type C optimization loops are blocked on this proposal landing.

## P8. Mode Selects an Invoker, Not an Execution Path

**Category:** execution, future **Relates to:** section 6.3 (step modes), section 11
(`for_each`), [process-framework-concepts.md](process-framework-concepts.md) § Core
Objects

`_execute_step` forks on all four modes and each branch owns its own execution path, so
a capability exists wherever someone implemented it rather than wherever it applies.
Fan-out was written for `mode: agent` and, until recently, existed nowhere else; a
`mode: code` step with `for_each` planned as a fan-out and then executed once with no
item bound. Admission still reaches only some paths.
`mode: composite` cannot fan out at all.

None of these gaps is a policy.
`for_each` states a step’s relationship to a roster, and nothing in that statement is
agent-specific.

The concepts doc already names the pivot the runtime does not honor:

> **Task:** one step applied to one item … the task is the pivotal object in this model,
> because it is the correct unit of scheduling, of failure, and of resume.

Three independent concerns are fused into `mode`:

1. **Mapping**, step to tasks: one task, or one per roster item.
   A property of the step’s relationship to data.
2. **Invocation**, how one task is performed: agent CLI, handler, command, human, child
   spec.
3. **Governance**, when and where a task may run: admission, concurrency ceiling, retry,
   placement.

Only the second varies by mode.

Proposal:

- Define `Invoker.run(task) -> disposition`, one implementation per mode.
  `mode` selects an invoker and decides nothing else.
- Expand mapping into tasks before an invoker is chosen, so fan-out is mode-independent.
- Attach admission, concurrency, and retry to tasks, so governance is mode-independent.
- Make blocking an invoker contract: either every invoker is awaitable, or the single
  call site wraps synchronous ones off the loop, rather than each branch deciding.

What it closes without separate implementation:

- `mode: composite` gains fan-out, and the consumer workaround of a code handler
  launching one child run per item stops being necessary.
- Every mode gains admission, retiring the remainder of design test 7.
- A per-step concurrency ceiling, currently absent, becomes one governance property
  rather than four parallel implementations.

The authored surface does not change.
A spec still says `mode: code` and `for_each` and means what an author already expects;
the runtime stops treating those two words as one decision.

Why it remains future work:

- The four paths differ in real ways beyond invocation, particularly the agent path’s
  adapters, variants, execution profiles, and auth pools.
  Collapsing them is not mechanical.
- The two sharpest gaps have been closed pointwise (code fan-out, off-loop handlers),
  which lowers the pressure without removing the cause.
- It overlaps the execution-model work, where the task is already the scheduled unit;
  doing both at once risks two half-migrations.

What the evidence supports firmly is narrower than the full refactor: mapping and
governance do not belong to `mode`, and every place they are attached to it has produced
either a missing capability or a consumer routing around one.

## Summary Matrix

| Proposal | Category | Status | Effort |
| --- | --- | --- | --- |
| P1. `reduce` CLI command | execution | implementation needed | medium |
| P2. Capability / policy split | authored, future | deferred | large |
| P3. Prepare / map-items model | authored, future | deferred | large |
| P4. Workspace promotion | authored, future | deferred | medium |
| P5. Subagent adapter | execution, future | deferred | medium |
| P6. Declarative run source | authored, future | deferred | medium |
| P7. Sweep / ensemble / experiment | authored, future | deferred | large |
| P8. Mode selects an invoker | execution, future | deferred | large |

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
