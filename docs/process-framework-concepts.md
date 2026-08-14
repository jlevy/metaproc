# Process Framework Concepts

The abstract execution model beneath any process framework: what such a system must
decide, the vocabulary for talking about it precisely, and the design tests that follow.
Nothing in the body is specific to one framework or one domain — the same model
describes a build system, a data pipeline, a batch of agent jobs, or a render farm.
A closing section maps the vocabulary onto Metaproc so the general model and the
concrete implementation can be compared directly.

## The Four Questions

A process framework runs a big job made of many small pieces of work.
Every such system, whatever its domain, has to answer four questions:

1. **What work exists?** — planning
2. **What order must it happen in?** — dependencies
3. **How much may run at once?** — resources
4. **What happens when something dies halfway?** — state and resume

The whole model compresses into one sentence: *planning produces a set of tasks;
dependencies decide when each task is ready; resources decide which ready tasks may
actually start; durable state makes all of it survivable.*

Everything below defines the terms in that sentence, one at a time, and then examines
the design choices inside each.

## Core Objects

**Artifact.** A durable input or output — typically a data file or document: a fetched
dataset, a rendered report, a validation result, a summary.
Artifacts are what steps consume and produce, and pointing at them (rather than at
in-memory state) is what makes work inspectable and resumable.

**Step.** A named unit of work in a recipe: “fetch the source data”, “render the
report”, “validate the output”.
A step declares its input and output artifacts.

**Process spec.** The recipe: a declaration of the steps and the ordering constraints
between them. A spec describes *shape*; it does not by itself say how wide any part of
the work is.

**Item.** One element of a collection the same step applies to: one document in a
corpus, one file in a dataset, one record in a batch, one entity under analysis.
The list of items a step maps over is its **roster**.

**Task.** One step applied to one item — the pair `(step, item)`. A step with no roster
is a single task.
The task is the pivotal object in this model: it is the correct unit of
scheduling, of failure, and of resume.
Frameworks that treat the *step* as the unit for those three things inherit every
problem described below.

**Run.** One execution of a spec against concrete inputs, with its own durable state.

## Planning

**Static planning** means the full set of tasks is computable before anything executes:
read the spec, resolve parameters, and the complete plan exists *as data*. The value is
everything you can do with a plan that is data: validate it, print it without running
(dry-run), diff it against another run’s plan, and resume deterministically because
replanning yields the identical plan.

**Dynamic planning** means some work is only knowable mid-run, because a step’s output
determines what later work exists.
The canonical shape: survey a large collection cheaply, then send only the interesting
subset through expensive work.
The roster of the later step *is an artifact produced by an earlier step*.

Full dynamism — steps themselves created at runtime — maximizes flexibility and destroys
most of static planning’s value: the plan can no longer be validated, displayed, or
diffed ahead of time, and resume requires replaying decisions.
There is a disciplined middle, and it is the important design point:

> **Static shape, dynamic width.** The steps and their edges are fixed by the spec; the
> roster of any step may be produced mid-run.
> The recipe never changes while running; only how wide each map is.

This keeps nearly all the flexibility that real workloads need while keeping the plan a
validatable, diffable object.

## Fan-Out and Fan-In

**Fan-out** is *map*: one step applied to every item in a roster.
The author writes the step once; planning (or mid-run roster discovery) turns it into N
tasks.

**Fan-in** is the reverse: a step that consumes the results of many tasks and produces
one artifact — a comparison, a merge, a selection.
A fan-in point is also called a **barrier**, because work behind it waits.

Every barrier forces a policy decision that must be *declarable*, not hard-coded — the
**join policy**: does the barrier fire when all upstream tasks **succeed**, or when all
have **finished either way**, or when some threshold is met?
“Select the best results from whichever items completed, and report the failures as
ineligible” is a legitimate and common join policy; a framework that only implements
“all must succeed” forces workflows with tolerable partial failure to move the barrier
outside the framework.

The composition of these two — fan-out, barrier that emits a smaller roster, fan-out
over that roster — is the survey-then-deepen pattern, and it is expressible entirely
with map, join policy, and dynamic width.
No additional primitive is required.

## Dependencies and Granularity

A dependency edge says “B waits for A”. The subtle and consequential question is
**granularity**:

- A **step-scoped edge** means every task of B waits for *every* task of A. This is a
  barrier between the two steps.
- An **item-scoped edge** means task B applied to item *x* waits only for task A applied
  to item *x*. Items flow through the chain independently — a **streaming pipeline**.

Neither is more correct in general; they are different statements about the data.
“Compare all survey results” genuinely depends on every result: step-scoped.
“Interpret item *x*’s data” genuinely depends only on *x*’s data: item-scoped.
The correctness principle:

> **The scheduler must enforce exactly the declared data dependencies — no more.** An
> edge the data does not require is a *false edge*, and every false edge converts
> independent work into waiting.
> The common failure is structural: an executor that synchronizes at step boundaries
> (or, coarser still, at whole *levels* of the graph) silently imposes step-scoped
> semantics on chains whose true dependencies are item-scoped, and the cost lands as
> fast items idling behind slow strangers.

When chained steps map over the same roster — or a downstream roster is a subset of the
upstream one, as in survey-then-deepen — item alignment is the natural default meaning
of an edge, keyed by item identity, with step-scoping as the explicit opt-out.

Granularity also governs **failure propagation**. With item-scoped edges, a failed task
blocks only its own item’s descendants; the step finishes *partial* and the run’s policy
decides the overall verdict.
With step-scoped edges, one failed item blocks the entire downstream graph.
A framework whose workflows routinely tolerate partial batches must propagate failure at
the granularity of the edges, or authors will route around it.

## Resources: Readiness Versus Admission

“May this task run *in principle*” and “may it start *right now*” are different
questions, and conflating them is a classic design error.
**Readiness** is a dependency fact: the task’s edges are satisfied.
**Admission** is a resource decision: given what is running and what the machine and the
outside world can bear, this ready task may start.
The scheduler owns readiness; a distinct admission layer owns starting.

Admission is governed by ceilings, and the organizing principle is that **each ceiling
lives at the scope of the truth it tracks**:

| Ceiling | Truth it tracks | Correct scope |
| --- | --- | --- |
| Memory / machine load | the host | global — one per machine, shared by all steps and all concurrent runs |
| Rate limits, quotas | an external service | per provider, shared by every task that calls it |
| Politeness caps | a human choice ("no more than 5 at once") | per step, optional |
| Operator cap | an incident intervention | global, temporary |

Two consequences follow.
First, a per-step concurrency *number* is the wrong home for a machine truth: N steps
with individually “safe” numbers still overcommit the host jointly, and the author is
being asked to answer a question ("how much memory is free?") that only measurement can
answer. Second, the machine ceiling should be **adaptive** — measured and re-decided
continuously — because a fixed conservative cap fails in both directions: it cannot back
off under pressure (so overload still happens) and cannot ramp when the host is idle (so
capacity is wasted).
A fixed cap is not a safety mechanism; it is the removal of one.

A related diagnostic for whether resource control lives in the right layer: **is the
tuning knob a config field or a paragraph?** When operators maintain prose instructions
for sizing concurrency by hand, the controller that should own that decision is missing.

## Durable State and Resume

The task is the unit of completion.
Each task’s terminal state (completed, failed, and any per-task detail) is recorded
durably — on disk, beside the artifacts — the moment it is known, never only in the
memory of the running orchestrator.

Then **resume is a rebuild, not a replay**: rerunning a run means recomputing the plan,
reading the recorded task states, reconstructing the ready set, and continuing.
Resume must be the normal operating mode, not a special recovery path:

- rerunning safely skips completed tasks;
- failed tasks are retryable without manual cleanup;
- stale “running” markers left by a killed orchestrator are reclaimable by evidence
  (owner liveness), not by hand;
- completion is judged by recorded state *plus validated output artifacts* — a partial
  artifact alone is never success;
- forcing re-execution invalidates a task and its dependents explicitly, leaving an
  audit trail.

Partial success is a first-class outcome, not an error state.
A run in which some items failed still has a definite, inspectable result: which tasks
completed, which failed, what the join policies did about it.

## The Execution Loop

The whole model, assembled:

> Planning turns the spec into tasks (statically, plus rosters discovered mid-run).
> A task is **ready** when its edges — at their declared granularity — are satisfied.
> One scheduler holds the ready set.
> One admission layer starts ready tasks as its ceilings allow.
> Each completion is durably recorded, may make dependent tasks ready, and may (via a
> produced roster) create new tasks.
> The loop ends when no task is ready, running, or awaited.
> Resume rebuilds the ready set from recorded state and re-enters the same loop.

Every concept above is one clause of this loop; a framework is complete when every
clause has an owner and minimal when nothing else does.

## Design Tests

Compact questions for evaluating a framework, or a proposed change to one.
Each traces to a section above.

1. **Is the plan data?** Can it be validated, printed, and diffed without executing?
2. **Is the shape static and the width dynamic?** Can a mid-run artifact serve as a
   later step’s roster, without steps themselves appearing at runtime?
3. **Is the task the unit** of scheduling, failure, and resume — or does the step leak
   into any of those roles?
4. **Do edges carry their true granularity?** Can an item-scoped chain stream, or does
   the executor introduce false edges at step or level boundaries?
5. **Are join policies declarable?** Can a barrier tolerate failed items where the
   workflow does?
6. **Is admission separate from readiness**, with each ceiling scoped to the truth it
   tracks — and is the machine ceiling adaptive rather than a hand-set number?
7. **Is every launch admitted?** Do any execution paths start subprocesses outside the
   admission layer?
8. **Is resume a rebuild?** Does rerunning skip completed tasks, retry failed ones, and
   reclaim stale markers — without manual cleanup?
9. **Is partial success first-class?** Does a run with failed items produce a definite,
   inspectable verdict?
10. **Is the knob a config field or a paragraph?** Does any operational sizing decision
    live in prose instructions instead of the controller?

A workflow forced to answer “no” by building its own coordinator on top of the framework
is the signal that the framework, not the workflow, needs the change.

## How Metaproc Maps to This Model

Concept by concept, with the authoritative doc for each.

| Concept | Metaproc today | Where |
| --- | --- | --- |
| Process spec, steps, artifacts | Markdown specs with typed `deps`/`inputs`/`outputs`; four step modes | [arch-metaproc-core.md §6](arch/arch-metaproc-core.md) |
| Static planning | spec → resolved `Plan` as data; `plan`, `--dry-run`, validation | [arch-metaproc-core.md §8](arch/arch-metaproc-core.md) |
| Dynamic width | fan-out rosters re-discovered at execution time, so a mid-run step may write a later step’s roster | [run_process.py](../src/metaproc/commands/run_process.py) (execution-time `discover_items_from_source`) |
| Fan-out | `for_each` over a declared items file; per-item retry with backoff | [arch-metaproc-core.md §6.7, §14.1](arch/arch-metaproc-core.md) |
| Task state and resume | `.state/tasks/{step}/{item}/status.yaml`; stale-marker reconciliation; output validation; `--force` invalidation | [arch-metaproc-core.md §10, §19.5](arch/arch-metaproc-core.md) |
| Admission | RunPool: adaptive memory ceiling, provider ceiling, operator cap, cross-run host admission, health, kill | [arch-runpool.md](arch/arch-runpool.md) |
| Same loop, other machines | two-tier cloud dispatch running the identical CLI against shared state | [arch-cloud-execution.md](arch/arch-cloud-execution.md) |

Known deviations from the model, current as of this writing — these are the active
design surface, not permanent properties:

- **Edge granularity (test 4).** Declared `needs` edges are step-scoped, and the
  executor is coarser still: it walks the step graph in topological *levels*, finishing
  each level before the next.
  Item-scoped edges do not exist, so chained fan-outs barrier at every step boundary.
- **Join policies (test 5).** Fan-in is implicitly “all items must succeed.”
- **Universal admission (test 7).** RunPool governs only the fan-out execution path; a
  step launched singly bypasses admission entirely.
- **Task-scoped operator surface (test 3, partially).** Force and resume selection are
  step-scoped (`--force`, `--from`, `--only`); there is no per-item force.

Design work addressing the first three — task-level ready-set scheduling with
item-aligned edges, declared join policies, and unified admission with layered ceilings
— is under active consideration; see [TODO.md](../TODO.md) and
[docs/metaproc-design-rev3-proposals.md](metaproc-design-rev3-proposals.md) for current
status.

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
