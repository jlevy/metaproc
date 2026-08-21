# Process Framework Concepts

The abstract execution model beneath any process framework: what such a system must
decide, the vocabulary for talking about it precisely, and the design tests that follow.
Nothing in the body is specific to one framework or one domain.
The same model describes a build system, a data pipeline, a batch of agent jobs, or a
render farm. A section near the end covers the loop layer that sits above single runs,
and a closing section maps the vocabulary onto Metaproc so the general model and the
concrete implementation can be compared directly.

## The Five Questions

A process framework runs a big job made of many small pieces of work.
Every such system, whatever its domain, has to answer five questions:

1. **Planning:** what work exists?
2. **Dependencies:** what order must it happen in?
3. **Resources:** how much may run at once?
4. **State and resume:** what happens when something dies halfway?
5. **Visibility:** can you see what is happening, and why?

The whole model compresses into one sentence: *planning produces a set of tasks;
dependencies decide when each task is ready; resources decide which ready tasks may
actually start; durable state makes all of it survivable; and every part of it must be
observable while it runs and explainable after it fails.*

Everything below defines the terms in that sentence, one at a time, and then examines
the design choices inside each.

## Core Objects

**Artifact:** a durable input or output, typically a data file or document: a fetched
dataset, a rendered report, a validation result, a summary.
Artifacts are what steps consume and produce, and pointing at them rather than at
in-memory state is what makes work inspectable and resumable.

**Step:** a named unit of work in a recipe, such as “fetch the source data”, “render the
report”, or “validate the output”.
A step declares its input and output artifacts.

**Process spec:** the recipe, a declaration of the steps and the ordering constraints
between them. A spec describes *shape*; it does not by itself say how wide any part of
the work is.

**Item:** one element of a collection the same step applies to, such as one document in
a corpus, one file in a dataset, one record in a batch, or one entity under analysis.
Every item carries a stable identity, its **key**, and the list of items a step maps
over is its **roster**.

**Task:** one step applied to one item, the pair `(step, item)`. A step with no roster
is a single task, and when the same step and item deliberately run under more than one
configuration, the configuration axis (the **variant**) joins the identity.
The task is the pivotal object in this model, because it is the correct unit of
scheduling, of failure, and of resume.
Frameworks that treat the *step* as the unit for those three things inherit every
problem described below.

**Attempt:** one execution try for a task.
A task may have zero, one, or many attempts; each has its own identity, timing,
placement, and disposition.
The distinction matters because “retry the task” is underspecified without it, and
because a slow or partitioned attempt can finish *after* it has been given up on, which
the framework must survive (see § Idempotence).

**Commit:** the single durable fact that a task published a complete, validated set of
outputs.
A commit is distinct from process exit, from output files existing, and from any
mutable status display.
It is the fact that downstream scheduling, caching, and lineage trust.

**Expansion:** the durable materialization of one fan-out, recording which items a step
actually mapped over: the roster’s key set, where it came from, and whether it is
**closed**, meaning no further items can appear.
Expansions are the bridge between a static recipe and dynamic width (see § Planning).

**Run:** one execution of a spec against concrete inputs, with its own durable state.

The authored surface does not need to expose all of these.
A framework can keep specs short and let its compiler and runtime own attempts, commits,
and expansions as internal records.
What it cannot do is conflate them, because each is a distinct durable fact with a
distinct owner.

## Contracts: Inputs, Outputs, and Keys

A step’s inputs and outputs are not incidental.
Declaring them, typed, is what turns a script into a process.
The declaration is a **contract**: which artifacts a step consumes, which it produces,
what shape each has, and which fields of that shape the framework itself depends on.

Most of an artifact’s content is **payload**, which the framework never looks inside and
should not, because domain structure belongs to the domain.
But a small set of fields are structural, and the framework must know them:

- The **item key**, the stable identity that names a task, addresses its state, aligns
  edges between steps, and deduplicates work across resume.
  A key must be stable across reruns, derived from the data rather than from execution
  order, or every property built on it silently breaks.
- The **key space** the key belongs to: a declared identity domain, such as “listed
  instrument” or “document identifier”, within which keys are unique and comparable.
  Two rosters can both contain the string `ACME` while meaning different things, so
  equality of strings is not identity; keys align only within one key space, with the
  lineage to prove it (see § Dependencies).
  Real identities are often compound, combining an entity with an as-of date, a
  scenario, a source, or a variant; the key space declares that structure once, and
  displays may flatten it.
- **Dispatch fields**, the handful of item fields the framework binds into a task’s
  invocation: the arguments the step actually varies on.
- **Grouping and ordering keys**, where fan-in needs them: which key groups many task
  outputs into one reduction, and what order the group is consumed in when the reduction
  is order-sensitive.

These structural fields exist to drive **dispatch**, the routing of work and data
through the process: which task an item becomes, which invocation receives which
arguments, which reduction a task’s output joins, which dependent a completion wakes,
which worker a partition of the roster lands on.
The ownership principle:

> **The domain declares the fields that govern routing; the framework owns the routing
> itself.** Dispatch logic, meaning partitioning, grouping, aligning, and distributing,
> is framework machinery driven entirely by declared keys, never hand-written inside
> steps.

MapReduce is the classic demonstration.
Its entire programming model is dispatch by key structure: map emits `(key, value)`
pairs, and the *shuffle*, the framework-owned dispatch step, routes every value to the
reducer its key selects, groups values within each destination, and, with a secondary
sort key, orders each group.
Authors write map and reduce; nobody writes the shuffle.
A general process framework needs far less than MapReduce’s fixed two-phase shape, but
the lesson transfers whole: **the fields the framework must understand are exactly the
keys that drive dispatch, meaning identity, alignment, grouping, ordering, and
placement, and everything else is payload.** A framework that reaches deeper into the
payload couples itself to one domain; one that knows less than this cannot schedule,
align, or resume correctly.
When domain code finds itself hand-routing items to workers or hand-collecting outputs
into groups, it is rebuilding the dispatch layer the framework should own.

Contracts are also where validation lives.
A step’s completion claim is checked against its declared outputs, verifying that the
artifacts exist and parse as their declared shape, so a half-written file can never
masquerade as success.
The validated result is published as one commit covering the full output set, so a crash
between two output files can never leave a half-visible completion (see § Idempotence).
A commit should also carry **provenance**: the upstream commits it consumed, the input
fingerprints, and the code, configuration, and as-of parameters that produced it, so any
artifact can answer where it came from without re-deriving anything.

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

Full dynamism, meaning steps themselves created at runtime, maximizes flexibility and
destroys most of static planning’s value: the plan can no longer be validated,
displayed, or diffed ahead of time, and resume requires replaying decisions.
There is a disciplined middle, and it is the important design point:

> **Static shape, dynamic width.** The steps and their edges are fixed by the spec; the
> roster of any step may be produced mid-run.
> The recipe never changes while running; only how wide each map is.

Stated precisely, the plan is two things.
The **template graph** is static and fully validatable up front: every step, every edge,
every contract. The width arrives as **expansion records**: when a roster materializes,
the framework durably records the item set, its key space, its producer, and,
critically, that the set is **closed**, meaning complete for this generation.
The effective task graph is the template graph plus its expansion records, and resume
reads the records rather than re-deriving and reinterpreting a mutable roster.

Closure is not bookkeeping; it is what makes fan-in sound.
A barrier that fires because “every item currently visible is finished” can fire while
the roster is still being written and silently consume a partial universe.
A fan-in is satisfiable only over a *closed* expansion.
The distinction also settles the empty case: an empty but closed roster legitimately
satisfies “all of them”, while an absent or still-materializing roster satisfies nothing
yet. And regenerating a roster never mutates a consumed item set in place: rerunning the
producer creates a new expansion generation, and work derived from the old one becomes
identifiably stale rather than silently mixed.

This keeps nearly all the flexibility that real workloads need while keeping the plan a
validatable, diffable object at every moment: known widths are recorded, pending widths
are visibly pending.

## Fan-Out and Fan-In

**Fan-out** is *map*: one step applied to every item in a roster.
The author writes the step once, and planning, or mid-run roster materialization, turns
it into N tasks under one expansion.

**Fan-in** is the reverse: a step that consumes the results of many tasks and produces
one artifact, such as a comparison, a merge, or a selection.
A fan-in point is also called a **barrier**, because work behind it waits.
When a fan-in reduces groups rather than everything at once, the grouping key and any
ordering key come from the contract, and the routing of outputs into their groups is
framework dispatch rather than step code (see § Contracts).

Every barrier forces policy decisions that must be *declarable* rather than hard-coded:

- **Which outcomes satisfy it.** Requiring that upstream tasks **succeeded** and
  requiring that they **finished** either way are different statements, and both are
  legitimate. “Select the best results from whichever items succeeded, and report the
  failures as ineligible” is a common and valid barrier.
  A framework that only implements “all must succeed” forces workflows with tolerable
  partial failure to move the barrier outside the framework.
  Name the two policies unambiguously; “completed” reads as either and should name
  neither.
- **How many are needed.** Usually all of them, sometimes any, occasionally a threshold.
- **What the consumer receives.** A fan-in consumer should be handed a typed collection:
  the successful artifacts *and* outcome descriptors for the failures, cancellations,
  and skips, so coverage is first-class data rather than something reconstructed by
  walking directories.

And every barrier fires only over a closed expansion (see § Planning), regardless of
policy.

The composition of these pieces, meaning fan-out, then a barrier that emits a smaller
roster, then fan-out over that roster, is the survey-then-deepen pattern, and it is
expressible entirely with map, barrier policy, and dynamic width.
No additional primitive is required.

## Dependencies and Granularity

A dependency edge says “B waits for A”. Stated precisely, an edge is a **dependency
clause** with four independent axes:

1. **Mapping:** which upstream tasks relate to this downstream task?
   The same item’s task (item-aligned), every task (collect), one task’s output shared
   by all (broadcast), or a keyed group.
2. **Requirement:** which upstream outcomes satisfy the clause, succeeded or merely
   finished?
3. **Cardinality:** all of the related tasks, any of them, or a threshold.
4. **Binding:** which committed outputs are delivered to the consumer, and in what form:
   one artifact, a typed collection, an ordered group.

Collapsing these axes into one step-level flag works only until a single consumer has
heterogeneous inputs, such as a barrier that needs *all* survey results finished either
way, *and* a broadcast policy artifact, *and* at least one benchmark that succeeded.
The authored surface can stay short, because a bare edge between two steps mapping the
same roster resolves unambiguously; the resolved plan should record the explicit clause
the shorthand meant.

The mapping axis deserves its own statement, because it is where the most wall-clock
hides:

- A **step-scoped edge** means every task of B waits for *every* task of A. This is a
  barrier between the two steps.
- An **item-scoped edge** means task B applied to item *x* waits only for task A applied
  to item *x*. Items flow through the chain independently, forming a **streaming
  pipeline**.

Neither is more correct in general; they are different statements about the data.
“Compare all survey results” genuinely depends on every result, so it is step-scoped.
“Interpret item *x*’s data” genuinely depends only on *x*’s data, so it is item-scoped.
The correctness principle:

> **The scheduler must enforce exactly the declared data dependencies, no more.** An
> edge the data does not require is a *false edge*, and every false edge converts
> independent work into waiting.
> The common failure is structural: an executor that synchronizes at step boundaries, or
> coarser still at whole *levels* of the graph, silently imposes step-scoped semantics
> on chains whose true dependencies are item-scoped, and the cost lands as fast items
> idling behind slow strangers.

Item alignment is inferable only where identity is provable: the two steps map the same
expansion, or one roster declares itself derived from the other within the same key
space. Matching key *strings* across unrelated rosters is coincidence, not identity, and
a framework that aligns on it will silently join unrelated work.
Where lineage cannot be proven, alignment must be declared explicitly.

Two stability rules complete the picture.
**Operator flags must not change edge semantics:** an option that governs whether the
run aborts on failure may change the run’s verdict, never whether a dependency counts as
satisfied, or the same spec means a different graph under different invocations.
And **causal meaning is versioned:** what a spec’s edges mean is part of the spec’s
semantics version, the resolved plan persists the meaning that was in force, and resume
executes the persisted plan.
A framework upgrade must never silently change what an existing spec’s edges mean.

Granularity also governs **failure propagation**. With item-scoped edges, a failed task
blocks only its own item’s descendants; the step finishes with partial coverage and the
run’s policy decides the overall verdict.
With step-scoped edges, one failed item blocks the entire downstream graph.
A framework whose workflows routinely tolerate partial batches must propagate failure at
the granularity of the edges, or authors will route around it.

## Resources: Readiness Versus Admission

“May this task run *in principle*” and “may it start *right now*” are different
questions, and conflating them is a classic design error.
**Readiness** is a dependency fact: the task’s clauses are satisfied.
**Admission** is a resource decision: given what is running and what the machine and the
outside world can bear, this ready task may start.
The scheduler owns readiness; distinct admission authorities own starting.

Admission is governed by ceilings, and the organizing principle is that **each ceiling
lives at the scope of the truth it tracks**:

| Ceiling | Truth it tracks | Correct scope |
| --- | --- | --- |
| Memory and machine load | the host | per host, shared by all steps and all concurrent runs on that host |
| Provider concurrency and rate | an external service’s quota | per quota namespace: provider, account, and model or region as applicable, often shared across hosts and runs |
| Politeness caps | a human choice ("no more than 5 at once") | per step or per declared resource, optional |
| Run budget | a hard spend or call ceiling | per run, as a durable reservation ledger |
| Operator cap | an incident intervention | global or scoped, temporary and observable |

Two consequences follow.
First, a per-step concurrency *number* is the wrong home for a machine truth: N steps
with individually “safe” numbers still overcommit the host jointly, and the author is
being asked to answer a question, “how much memory is free?”, that only measurement can
answer. Second, the machine ceiling should be **adaptive**, measured and re-decided
continuously, because a fixed conservative cap fails in both directions: it cannot back
off under pressure, so overload still happens, and it cannot ramp when the host is idle,
so capacity is wasted.
A fixed cap is not a safety mechanism; it is the removal of one.

What a task asks admission for is a **resource claim**: the capacities it needs, such as
estimated memory, a unit of provider concurrency in a named quota namespace, or budget
units.
A first implementation may honor only a slot count and an estimated footprint, but
the request should be shaped as a claim from the start, because “one task equals one
slot” quietly becomes the permanent resource model otherwise.

When more tasks are ready than admission allows, start order is scheduling policy, and
it should be deterministic and observable: due retries before first attempts, aging so
long-waiting work eventually runs, fair rotation across steps or scopes so an early wide
map cannot starve later stages.
Reserving minimum capacity per step is the blunter instrument; prefer fairness and
aging, and add reservations only when a measured workload proves they are needed.

These concerns also separate vertically, into four layers.
One logical **scheduler** per run derives readiness; **admission authorities** grant
capacity at their own scopes; a **placement** decision chooses where an attempt runs,
locally or on some worker; an **executor** launches and supervises it there.
Keeping the layers apart is what lets the same run move between one laptop and a fleet
of machines without changing what the spec means: the run’s logical scheduler is one
authority even when execution spans many hosts, and no single pool object is the
scheduling contract.

A related diagnostic for whether resource control lives in the right layer: **is the
tuning knob a config field or a paragraph?** When operators maintain prose instructions
for sizing concurrency by hand, the controller that should own that decision is missing.

## Idempotence, Durable State, and Resume

The task is the unit of completion, the attempt is the unit of execution, and the commit
is the unit of trust.
Keeping the three distinct is what makes recovery safe when execution is concurrent,
distributed, or merely unlucky.

The lifecycle of a healthy attempt:

1. **Staging:** the attempt writes its outputs privately, scoped to the attempt, never
   directly into the shared namespace.
2. **Validation:** the declared contracts are checked against the staged outputs.
3. **Commit:** one atomic, immutable record publishes the complete validated output set.
   Downstream consumers see outputs only through commits.
4. **Projection:** human-facing status is updated *from* the durable facts.
   Projections are rebuildable summaries, never the source of truth.

**Idempotence is the property that makes every recovery mechanism safe.** A task will
sometimes execute more than once: a retry after a transient failure, a resume after a
crash, an orchestrator that died mid-write and was replaced while its worker survived.
Repeated execution must never corrupt anything.
Concretely:

- attempts are append-only history with stable identities, never overwritten in place;
- outputs stay attempt-private until commit, so a torn attempt leaves nothing visible;
- at most one commit is accepted per task per causal generation;
- late attempts are **fenced**: an attempt that was superseded, because its lease was
  reclaimed or a newer attempt was granted, must be refused at commit time even if its
  process exits successfully.
  Reclaiming a stale lease is not enough, because the old holder may still be running
  and may finish later; the commit gate is what makes reclamation safe.

Note what is *not* required: determinism.
A step backed by a model or an external service may produce a different but equally
valid output on each attempt.
What must be exactly-once is the **commit**, not the computation.

Retry and re-execution then separate cleanly into two ideas.
A **retry** is a new attempt at the same task, warranted when the failure’s evidence
classifies as transient (a rate limit, a timeout, a connection failure) and bounded by a
declared policy of attempts and backoff.
A **force**, or any change to a task’s inputs, is a new *generation* of the task: the
old commit becomes stale, and staleness propagates to downstream tasks along the same
dependency mappings that scheduling uses, exactly as far as the data reaches and no
farther.
Failure classification itself is two-axis: what the engine should do (retry now,
retry later, fail permanently, mark lost) and what happened (rate limit, crash, invalid
output, and so on). Domain verdicts, such as “this item legitimately has no answer”, are
successful outputs, not failures at all.

With those facts durable, **resume is a rebuild, not a replay**: rerunning a run means
recomputing the plan, reading expansions, attempts, and commits, reconstructing the
ready set, and continuing.
Resume must be the normal operating mode, not a special recovery path:

- rerunning safely skips committed tasks;
- failed tasks are retryable without manual cleanup;
- stale “running” markers left by a dead orchestrator are reclaimable by evidence, and
  the fence, not the reclamation, protects against the marker’s owner still being alive;
- completion is judged by recorded commits, never by output files existing;
- forcing re-execution is an explicit, audited generation change, not a directory
  deletion.

Completion also has a scope.
Within a run, task identity decides what resume skips.
Across runs, reuse requires a declared key: which inputs and which semantically
meaningful parameters make two tasks the same work (a model name does; a run identifier
does not). With durable commits and a declared reuse key, a new run can adopt prior work
soundly; without one, reuse degrades into re-running everything or guessing by filename.

Finally, keep three vocabularies apart, because they answer different questions:

- **Task outcomes** are durable facts: succeeded, failed, cancelled, or skipped with a
  reason. Dependency clauses consume these.
- **Scheduler views** are derived, momentary answers to “why is this not running”:
  waiting on a clause, waiting for an expansion to close, in retry backoff, waiting for
  admission, running. They are projections, not stored truth.
- **Aggregates** summarize many tasks: a step or run is succeeded, failed, partial,
  cancelled, or incomplete.
  Partial coverage with explicit failure records is a normal, useful product state, not
  an error state, and it is an aggregate; no individual task is ever “partial”.

## Effects and Finalization

Most task outputs are artifacts inside the run.
Some outputs are **effects**: a publication to durable storage, a notification, a
registration in an external system, an opened pull request.
Effects differ from artifacts in one essential way: the outside world saw them, so crash
recovery cannot simply throw them away and try again blindly.

Two disciplines make effects safe:

- Every effect carries an **idempotency key** and records a **receipt**: what was
  attempted, against which target, with what observed result.
  A retried effect with the same key must be recognizable, by the adapter or the target,
  as the same effect.
- Effects that deliver a run’s results run against a **frozen snapshot**: finalization
  first quiesces the run, meaning no further ordinary commits may enter the delivered
  generation, then produces the final manifest, and only then delivers it.
  Delivering a still-mutating tree is self-contradictory, because the delivered digest
  and the final state cannot both be right.

Analytical completion and delivery completion are distinct outcomes and should be
visible separately: a run whose analysis succeeded but whose publication is retrying is
a delivery problem, and conflating the two turns a delivery hiccup into apparent
recomputation work.

## Visibility

A system can be correct on the first four questions and still be impossible to operate,
because operating a run is mostly asking three things: what is it doing, why is that
task stuck or failed, and what would make the whole thing faster?
Visibility is a top-level question rather than a logging afterthought, and it decomposes
into four views:

- **Run state:** what is completed, running, failed, blocked, and retrying, per task,
  answerable from durable state without grepping logs, and aggregable upward per step,
  per run, and across runs.
- **Blocker explanation:** for every task that is not running, one primary reason with
  supporting detail: the exact unmet clause and which upstream tasks fail it, the
  expansion that has not closed, the retry timestamp it is waiting for, the specific
  resource ceiling it is queued behind, the manual approval it awaits, or the stale
  generation that superseded it.
  “Pending” is not an answer; a scheduler that knows why a task cannot run should say
  so.
- **Failure explanation:** every failed task carries its evidence, meaning a classified
  failure reason, the log tail, and the inputs it saw.
  “Why did this fail” must be answerable from the task’s record alone; a failure whose
  explanation requires re-running it is a visibility bug.
- **Dependency and resource attribution:** the declared graph and its *current* blocking
  structure, meaning which edges gate which waiting tasks and which running tasks sit on
  the critical path holding up the most downstream work; and, when a run is slow, which
  constraint binds, whether computation, memory pressure, an external service, or the
  process’s own shape as false edges and barriers serialize what could stream.
  These interact, since a memory ceiling lowers concurrency, which stretches the
  critical path, so the layers must be inspectable *together* or operators will fix the
  wrong one.

Two principles govern the implementation.
**Views are projections of the same durable state that drives execution.** A second
bookkeeping system maintained for reporting will disagree with the first at exactly the
moments it matters, meaning mid-incident, after a crash, and during a resume, and every
tool built on the second system inherits the disagreement.
And **state answers what is true now; an append-only event history answers what happened
and when.** Both are durable; the history is what failure analysis and performance
attribution read. Projections themselves should be typed and versioned so tools built on
them do not break as the schema grows.

## The Execution Loop

The whole model, assembled:

> Planning resolves the template graph; execution materializes expansions and closes
> them. A task is **ready** when its dependency clauses, at their declared mapping and
> requirement, are satisfied over closed expansions.
> One logical scheduler holds the ready set.
> Admission authorities grant resource claims at their own scopes; placement chooses
> where; an executor runs the attempt there.
> Each attempt stages privately, validates, and publishes at most one fenced commit per
> task generation; commits wake dependents and may materialize new expansions.
> Every transition lands in durable facts and an event history as it happens.
> The loop ends when no task is ready, running, or awaited; finalization freezes a
> snapshot and delivers it through receipted effects.
> Resume rebuilds the ready set from the durable facts and re-enters the same loop.

Every concept above is one clause of this loop.
A framework is complete when every clause has an owner and minimal when nothing else
does.

## Loops: Processes That Repeat

Everything so far describes one pass: plan, execute, finish.
There is a further layer, **iterative processes**, where a whole run is the body of a
loop that repeats until some condition holds.
An automated research loop is one example: gather sources, synthesize, identify gaps,
gather again, until coverage is sufficient.
Benchmark-driven improvement is another: propose a candidate change to an algorithm or
configuration, evaluate it against a fixed benchmark, accept or reject it, and repeat,
where the candidates are themselves durable artifacts being created, scored, and carried
forward.

A loop adds four elements on top of the single-run model:

- **Carried state:** durable artifacts that survive across iterations, such as the
  current best solution, the accepted candidates, and the accumulated evidence.
  Carried state is data with a contract, exactly like any other artifact.
- **A measurement step:** an evaluation producing comparable scores against a fixed
  reference, so iterations can be ranked rather than merely counted.
  The measurement’s own inputs and outputs are declared like any step’s; an unmeasured
  loop is just repetition.
- **An accept/reject gate:** the policy that decides whether an iteration’s output
  enters the carried state, whether by threshold, comparison against the incumbent, or
  review.
- **A termination policy:** a fixed iteration count, a budget, convergence, or
  no-improvement-in-k, declared so the loop’s cost is bounded before it starts.

The important structural fact is that **the loop sits above the run, not inside it.**
Each iteration is an ordinary run of a static-shaped process, and the loop adds carried
state, measurement, gating, and termination on top.
Framed this way, loops preserve every property of the single-run model: each iteration’s
plan is still static, resume still works per iteration, and the iteration history is
itself durable, inspectable data recording which iteration changed what, and why.
A framework therefore does not need runtime-mutating specs to support iteration.
It needs a home for carried state and a driver that reruns the process until the
termination policy fires.
Between full dynamism and the loop-above-runs model sits one disciplined intermediate: a
declared expansion point may emit a closed, typed follow-up roster under a hard item and
cost budget, which supports bounded adaptive research without runtime graph mutation.
Sweeps (same process, a grid of parameter values), ensembles (same process, many
variants, merged), and experiments (controlled comparisons between variants) are the
same layer: composition *over* runs, with runs unchanged beneath.

## Design Tests

Compact questions for evaluating a framework, or a proposed change to one.
Each traces to a section above.

1. **Is the plan data?** Can the template graph be validated, printed, and diffed
   without executing, and are materialized widths durable records rather than re-read
   mutable files?
2. **Is the shape static and the width dynamic, with closure?** Can a mid-run artifact
   serve as a later step’s roster, does the roster close explicitly, and does a barrier
   refuse to fire over an unclosed roster?
3. **Is the task the unit** of scheduling, failure, and resume, or does the step leak
   into any of those roles?
4. **Do edges carry their true granularity?** Can an item-scoped chain stream, or does
   the executor introduce false edges at step or level boundaries?
5. **Are dependency clauses expressive and explicit?** Can mapping, requirement,
   cardinality, and binding vary per input, is alignment inferred only from proven
   lineage, and does the resolved plan record what shorthand meant?
6. **Is admission separate from readiness**, with each ceiling scoped to the truth it
   tracks, and is the machine ceiling adaptive rather than a hand-set number?
7. **Is every launch admitted?** Is every independently scheduled task attempt admitted
   through the applicable authorities, with its full process tree charged to it?
8. **Is resume a rebuild?** Does rerunning skip committed tasks, retry failed ones, and
   reclaim stale markers, without manual cleanup?
9. **Is partial success first-class?** Does a run with failed items produce a definite,
   inspectable verdict, and do fan-in consumers receive outcome descriptors rather than
   only the survivors?
10. **Is the knob a config field or a paragraph?** Does any operational sizing decision
    live in prose instructions instead of the controller?
11. **Are contracts and keys declared?** Do steps declare typed inputs and outputs, is
    completion validated against them, and does every item carry a stable key in a
    declared key space, with all routing by those keys done by the framework rather than
    hand-written inside steps?
12. **Are attempt, commit, and task distinct?** Is attempt history append-only, is there
    at most one fenced commit per task generation, and can a late stale attempt never
    publish?
13. **Can you see why not?** For every non-running task, is one primary blocker reason
    inspectable from durable state, and can slowness be attributed among compute,
    memory, external services, and graph shape?
14. **Can it loop?** Can an iterative improve-measure-repeat process be expressed as
    repeated runs over durable carried state, with a declared measurement, gate, and
    termination, without mutating the spec at runtime?
15. **Are effects receipted and finalization frozen?** Do external effects carry
    idempotency keys and receipts, and are results delivered only from a quiesced
    snapshot generation?
16. **Is causal meaning versioned?** Are edge semantics part of a versioned spec
    contract, persisted in the resolved plan, so a framework upgrade cannot silently
    change what an existing spec means?

A workflow forced to answer “no” by building its own coordinator on top of the framework
is the signal that the framework, not the workflow, needs the change.

## How Metaproc Maps to This Model

Concept by concept, with the authoritative doc for each.

| Concept | Metaproc today | Where |
| --- | --- | --- |
| Process spec, steps, artifacts | Markdown specs with typed `deps`, `inputs`, and `outputs`; four step modes | [arch-metaproc-core.md §6](arch/arch-metaproc-core.md) |
| Contracts and keys | Declared `as:` and `parse:` shapes on deps; softschema-validated artifacts (`softschema inspect`, `softschema validate`); `for_each` `bind` and `bind_fields` declare the dispatch fields; the item key addresses per-task state | [arch-metaproc-core.md §6.5-6.7, §13](arch/arch-metaproc-core.md) |
| Static planning | Spec resolved into a `Plan` as data; `plan`, `--dry-run`, validation | [arch-metaproc-core.md §8](arch/arch-metaproc-core.md) |
| Dynamic width | Fan-out rosters re-discovered at execution time, so a mid-run step may write a later step’s roster | [run_process.py](../src/metaproc/commands/run_process.py) (execution-time `discover_items_from_source`) |
| Fan-out | `for_each` over a declared items file; per-item retry with backoff | [arch-metaproc-core.md §6.7, §14.1](arch/arch-metaproc-core.md) |
| Task state and resume | Per-item `status.yaml`, `attempt.yaml`, `result.yaml`; stale-marker reconciliation; `--force` invalidation with audit trail | [arch-metaproc-core.md §9-10, §19.5](arch/arch-metaproc-core.md), [artifact-catalog.md](artifact-catalog.md) |
| Admission | RunPool: adaptive memory ceiling, provider ceiling, operator cap, cross-run host admission, health, kill | [arch-runpool.md](arch/arch-runpool.md) |
| Visibility | `status` (with `--check`), `wait`, `tail`, `pulse`, `stats`, `deps`, `structure-report`, `pool status`, `pool events`, `pool health`, `pool concurrency-timeline`, `pool rollup`, `resource-report`, `trace`; classified `FailureClass` per item; Metabrowser plugin views | [arch-metaproc-core.md §9, §15](arch/arch-metaproc-core.md), [arch-runpool.md § Visibility Contract](arch/arch-runpool.md) |
| Distribution | Two-tier cloud dispatch running the identical CLI against shared state, partitioned per fan-out step | [arch-cloud-execution.md](arch/arch-cloud-execution.md) |

Known deviations from the model, current as of this writing.
These are the active design surface, not permanent properties.
Most trace to two founding assumptions that were reasonable when made: the step was the
original unit of execution and state, and one local orchestrator was the only writer.

- **Edge granularity (test 4).** Declared `needs` edges are step-scoped, and the
  executor is coarser still: it walks the step graph in topological *levels*, finishing
  each level before the next.
  Item-scoped edges do not exist, so chained fan-outs barrier at every step boundary.
- **Dependency clauses (test 5).** Fan-in is implicitly “all items must succeed”;
  mapping, requirement, and cardinality are not separately declarable, and fan-in
  consumers receive no outcome descriptors.
- **Closure (test 2).** Roster re-reading works today because the level walk guarantees
  the producing step finished before the consumer starts, so closure is implicit in the
  step boundary. An event-driven scheduler removes that implicit guarantee; explicit
  expansion generations and closure must come first.
- **Attempt, commit, and fencing (test 12).** Per-task state exists, but attempt history
  is a single fixed path rather than append-only records, outputs are not staged
  attempt-privately, there is no single commit record covering a multi-output task, and
  nothing fences a late stale attempt.
  Sufficient for one local writer; not for distributed retry.
- **Universal admission (test 7).** RunPool governs only the fan-out execution path, so
  a step launched singly bypasses admission entirely.
- **Task-scoped operator surface (test 3, partially).** Force and resume selection are
  step-scoped (`--force`, `--from`, `--only`); there is no per-item force, and
  invalidation is directory-shaped rather than causal.
- **Semantics versioning (test 16).** Process specs carry no semantics version, and the
  resolved plan is not persisted as the authority that resume executes.
- **Effects and finalization (test 15).** No finalization or receipted-effect protocol
  exists; delivering a run’s results is an out-of-band operator action.
- **Bottleneck attribution (test 13, partially).** Run, pool, and resource views are
  rich, but per-task blocker reasons and the current blocking structure are derivable
  from state rather than a first-class view.
- **Failure evidence (test 13).** A failed task records its reason as one string.
  Output validation produces structured records naming the failing field, the validator
  that refused it, and the value it saw, and those are formatted into a sentence before
  storage. The engine then recovers its own retry decision by substring-matching that
  sentence, which makes the decision sensitive to the artifact’s filename.
  A consumer wanting to know which invariant refused which output has the same substring
  matching as its only option, and one has written it.
  See
  [plan-2026-08-20-contract-failure-primitives.md](project/specs/active/plan-2026-08-20-contract-failure-primitives.md).
- **Loops (test 14).** Iteration is not first-class.
  The conceptual frame exists in
  [metaproc-concepts-and-principles.md §5](../src/metaproc/docs/metaproc-concepts-and-principles.md),
  covering the optimization loops, and sweep, ensemble, and experiment composition is a
  named proposal in
  [metaproc-design-rev3-proposals.md P7](metaproc-design-rev3-proposals.md); today a
  loop driver lives outside the framework.

Design work addressing the scheduling and state items, meaning task-level ready-set
scheduling over dependency clauses, expansion closure, attempt fencing with single
commits, and admission authorities at their true scopes, is under active consideration.
See [TODO.md](../TODO.md) and
[metaproc-design-rev3-proposals.md](metaproc-design-rev3-proposals.md) for current
status.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
