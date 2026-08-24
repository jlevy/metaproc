---
title: Native Mapped Composite Scopes
description: >-
  Add the missing composition of mapping and in-process process scopes, after unifying
  recursive run policy and host admission, without a child CLI or a new general
  scheduler.
author: Joshua Levy (github.com/jlevy) with LLM assistance
date: 2026-08-23
last_updated: 2026-08-23
status: Draft — Architecture Review
category: plan
---
# Feature: Native Mapped Composite Scopes

## Overview

Metaproc can already fan an agent or code step over a roster, run a composite process
in-process, propagate execution settings into that child, visualize composite source
graphs recursively, and govern fan-out subprocesses with RunPool.
It cannot combine the first two capabilities: `mode: composite` is rejected when it
declares `for_each`.

That one gap pushes a workflow with an existing multi-step process per item toward one
of two bad shapes. It must copy the child process’s leaves into a cohort-sized spec, or
map a code handler that launches `metaproc run-process` once per item.
A downstream cohort spike took the latter path.
It gives each proxy handler a child command and hides leaf work, resource use, failure
evidence, and execution settings behind that command.

This proposal adds the missing composition: an existing composite may be mapped over an
existing roster. Each item becomes an in-process child scope beneath the parent run.
The mapped executor can reuse the current recursive evaluator, neutral fan-out runner,
and leaf executors. The state, port, evidence, and recovery boundary around it is new
work and is specified explicitly below.

Before mapped scopes ship, pull request 31 lands and one internal execution context
carries run-wide policy, cancellation, credentials, and concurrency through every
recursive call. Before the feature is used for production cohorts, one host authority
must admit both RunPool and scalar launches against the same byte ledger.
These are safety prerequisites, not a second scheduler.

The proposal does **not** make the general ready-task scheduler in
[`execution-model-design.md`](../../../execution-model-design.md) a prerequisite.
The motivating workflow can express its cohort barriers explicitly—breadth, promotion,
depth, and review—and let each mapped item process use the established level walk
internally. The full task scheduler remains a valid later design if a workload
demonstrates the need for cross-scope streaming, changing expansion generations, or
multi-writer scheduling.

## Decision Summary

Implement the smallest safe stack in dependency order:

1. merge pull request 31’s attempt-history and `scope_path` slice;
2. introduce one `RunExecutionContext`, unify recursive policy and concurrency, pass
   credential-pool policy to scalar agents, and move blocking command work off the
   shared event loop;
3. permit `for_each` on composites by calling the neutral `run_fan_out` runner with a
   composite invoker, then add the required parent state, named-port, evidence, output
   validation, and per-item recovery semantics;
4. make one mutex-protected host byte authority govern both RunPool and scalar launches,
   including cold ramp and warm-state restoration; and
5. extend existing plan, status, trace, and Metabrowser projections to show mapped
   scopes and their artifacts.

Do not add a new mode, workflow service, scheduler DSL, provider ontology, artifact
registry, or agent serialization protocol.
Do not refactor all four execution modes behind a new `Invoker` hierarchy as part of
this work.

## Goals

- Map a reusable multi-step process over a declared roster without a domain handler or
  subprocess launching Metaproc.
- Keep one framework orchestration authority, one parent run identity, and one shared
  execution/admission context.
- Let mapped scopes start cheaply while only executable leaves consume resource
  capacity.
- Reuse execution-profile memory estimates, host admission, OS pressure telemetry,
  RunPool, credential pools, output validation, retry feedback, and task records.
- Preserve named, declared artifact boundaries between parent and child processes.
- Make one-item repair a normal framework operation: force an item or one of its child
  steps without launching a child CLI or editing state by hand.
- Make the authored process tree, mapped item scopes, outcomes, and produced artifacts
  visible through the existing inspection surfaces.
- Preserve current behavior for every existing process spec.
- Provide a production path that runs unchanged on a laptop or a high-memory GCE host,
  with capacity controlled by current headroom and the selected harness profile.

## Non-Goals

- Replacing the production level walk with a general ready-set scheduler.
- Implementing arbitrary dependency predicates, mutable rosters, speculative execution,
  multidimensional placement, or a database-backed control plane.
- Supporting multi-host concurrent writers to one run in this slice.
- Adding a second durable scheduler to RunPool.
- Requiring attempt-private publication, fenced commits, cross-scope causal
  invalidation, or budget reservations before the first single-host mapped workflow.
- Changing the contract-failure boundary established by pull requests 23 through 29.
  Agents continue to write declared files; Metaproc validates them and supplies
  structured corrective feedback under the existing policy.
- Creating a second artifact authority.
  Any runtime artifact index is a rebuildable view over declared outputs and task
  results.
- Encoding downstream stages, item types, promotion policy, or other domain terms in
  Metaproc.

## Background and Existing Capability

The proposal is intentionally incremental.
The relevant current behavior is:

| Concern | Existing Metaproc primitive | Required change |
| --- | --- | --- |
| Process composition | `mode: composite` loads a child spec and calls `_orchestrate()` in-process beneath the parent run | Reuse the evaluator per item, but remove its independent semaphore and incomplete policy propagation first |
| Mapping | Neutral discovery, key validation, `run_fan_out`, item paths, retry wrappers, and basic fan-in outcomes exist | Make mapped composites a caller of `run_fan_out`, not a third gather loop |
| Source visualization | `PlanBundle` and the viz projector recurse through composite children with qualified node IDs | Add the mapping declaration and runtime item instances |
| Artifact ports | Process input/output declarations and output re-exports exist | Add scoped composite bindings, child-boundary validation, output projection, and dependency-clause lowering |
| Execution policy | Some backend, profile, variant, auth, and cloud arguments propagate into composites | Carry all run policy in one context; characterize force, skip, continue, cancellation, and auth behavior |
| Command execution | Synchronous handlers are moved to a thread | Move command-backed code steps off the event loop too and give the run-owned executor an explicit ceiling |
| Fan-out resources | Each RunPool owns adaptive subprocess concurrency and takes count-only host slots | Keep RunPool execution, but make every actual launch claim bytes from one shared host authority and constrain ramp/restore with it |
| Scalar resources | Scalar agents and command-backed code bypass RunPool; scalar agents use a separate best-effort count gate | Use the same byte authority for child subprocesses and propagate credential-pool policy |
| Failure feedback | Structured output failures and bounded corrective prompts are merged on main through pull request 29 | Reuse unchanged inside mapped children |
| Task evidence | Leaf task records and basic item outcomes are durable; a scalar composite itself writes no task result | Add mapped-parent task state/results and outcome links to child evidence; merge pull request 31 first |

This proposal follows the concept stack rather than replacing it.
A mapped composite is a scope, not a resource-holding task.
Its child leaves remain the work units.
Readiness remains distinct from admission.
Files remain the universal step boundary.
Structure is declared in process specs, while run records and views are derived from
execution facts.

### Review basis and document precedence

The architecture review used the relevant historical and current documents as a stack,
not as a flat list of equally binding proposals:

| Source | Status in this proposal |
| --- | --- |
| Concepts and principles | Normative boundaries: files at steps, framework-owned orchestration/validation/publication, declarative structure, no domain scheduler or large ontology |
| Core architecture, conventions, developer guide, and operator reference | Authority for shipped process syntax, composite recursion, fan-out, state, failure, recovery, commands, and operator expectations |
| RunPool, memory-accounting, authentication, Claude harness, and cloud architecture | Authority for current subprocess supervision, pressure signals, profile/auth propagation, host coordination, and single-host versus multi-host deployment boundaries |
| File-I/O, artifact-catalog, visualization, and testing docs | Authority for existing file ownership, static artifact names, recursive process views, and verification tiers |
| Process-framework concepts | Conceptual tests plus an explicit inventory of current deviations; a gap is not automatically a requirement to implement every target semantic at once |
| Execution-model design and reference reducer | Settled target semantics for a future task scheduler; retained as the escalation design, not represented as current production behavior |
| Revision 3 proposals, especially P8 | Design warning that mapping, invocation, and governance are fused today; it explicitly cautions against combining a full invoker refactor with a scheduler migration |
| Performance notes | Measure the real workload first; avoid broad refactors and eager projections without evidence |

Recent pull requests fit the same stack:

- pull request 17 established the concept vocabulary;
- pull request 20 added the execution-model design and reference oracle, not a
  production scheduler;
- pull request 21 added scalar host admission with a deliberately compatible,
  best-effort posture;
- pull requests 23 through 29 preserved contract-failure facts, made output policy
  declarative, fixed conformance/retry boundaries, and fed bounded validation facts back
  to agents; and
- pull request 31 is the completed attempt-history/`scope_path` prerequisite for this
  work. It does not implement mapped scopes, output staging, generations, or the ready
  scheduler, and its unimplemented full commit model is not a gate.

The consequence is important: this proposal may reuse and extend shipped primitives, but
it may not describe a reference-model feature as already implemented or revive an older
proposal under a new name.

## Architecture Review

### Pull request 32 review disposition

The deep review approved mapped in-process composite scopes as the minimal primitive and
required the following corrections before implementation:

| Finding | Disposition in this revision |
| --- | --- |
| F1: sequencing and recursive policy | Pull request 31 is first. `RunExecutionContext`, one run semaphore, and characterized force/skip/continue/cancel propagation are Phase 1 prerequisites. |
| F2: state, ports, and evidence are new work | Phase 2 now includes mapped-parent task state/results, child-boundary validation, scoped namespaces, richer outcomes, and lower-layer spec loading. |
| F3: split memory authority and blocked event loop | Command work moves off-loop in Phase 1. Phase 3 uses one byte authority for pool and scalar launches and governs ramp and warm restore. |
| F4: scalar credential-pool bypass | Auth and pool dispatch become run-context policy, with pool-label assertions in M1. |
| F5: third fan-out path and ambiguous IDs | Mapped composites call `run_fan_out`; ports lower to dependency clauses; `/` identifies an item while `::` retains composite descent. |
| F6: per-item recovery | Item-scoped force, child propagation, resume-time output validation, and three-view consistency move into Phase 2. |
| F7: unreachable escalation tests | Derived-subset lineage and observable streaming/fairness triggers are restored; M4 and M5 measure barrier-drain idle. |
| F8: smaller seams | The plan standardizes roster input indirection, specifies the byte mutex/ledger, states the single-host cloud limit, gates M4 on write-boundary cost, and feeds measured harness RSS into profiles. |

### Alternatives considered

| Alternative | What it buys | Why it is not the default |
| --- | --- | --- |
| Flat cohort spec with `for_each` on every leaf | No Metaproc feature work | Duplicates or mechanically rewrites established child processes; complex children can have branching agent/code graphs; stage-level barriers replace per-item progress |
| Push the roster into every child process | Uses current composites without mapping them | Every reusable scalar process becomes a cohort process and must repeat item mapping across its leaves; composition becomes less reusable and failures are harder to read |
| Code fan-out that launches `run-process` | Small consumer patch | Creates a child command per item, loses one policy/admission context, flattens child failures, and hides the leaf graph; this is the rejected downstream spike |
| Static compiler that flattens every child leaf into the parent plan | One fully expanded graph | Requires early decisions about task keys, output rebasing, dynamic rosters, mode-independent invocation, and persisted semantics; substantially larger than the proven workload needs |
| General ready-task scheduler and mapped dependency clauses | Handles cross-scope streaming and more dynamic graphs | Correct long-term design when those semantics are required, but it also brings expansion closure, generations, fencing, fairness, new persistence, and compatibility work that the motivating workflow can avoid by putting each item chain inside one mapped scope |
| **Mapped in-process composite scope** | Reuses child specs, current recursion, task state, fan-in, and process views | Recommended; the remaining work is bounded and directly exercises existing primitives |

### Why recursive evaluation is not a second orchestrator

The current composite path does not start a second CLI, acquire a child orchestrator
lease, or establish an independent operator entrypoint.
It is a recursive evaluator inside the parent process.
This proposal keeps that property.

The implementation must make the boundary explicit:

- the top-level command creates the run identity and execution context;
- mapped composite scopes receive that context by reference;
- child scopes cannot override backend, profile files, auth policy, admission posture,
  or run-wide concurrency unless the parent explicitly declares a supported override;
- force selectors, skip policy, continue policy, and cancellation are interpreted once
  and propagated with characterized semantics;
- scope evaluation does not itself acquire an agent slot; and
- cancellation and fatal run policy flow downward from the parent.

Child scopes retain nested state and event directories because those are useful
addresses, not separate scheduling authorities.

### Escalation triggers for the general scheduler

Do not implement the production ready-task scheduler merely because the reference model
can describe it. Revisit it when at least one production workload requires one of these
properties and cannot be expressed cleanly with mapped scopes and explicit barriers:

- a fork or join must stream item-by-item across sibling mapped steps;
- a derived subset must stay item-aligned to its parent roster instead of closing and
  re-barriering;
- production measurements show useful downstream work waiting for an upstream roster to
  close, with material barrier-drain idle as a fraction of wall-clock;
- task wait-time skew across mapped scopes shows that host/provider admission cannot
  provide the required global fairness;
- an operator needs causal per-item force across scope boundaries;
- constrained multi-writer scheduling must extend beyond the current `gcp-worker` path;
  or
- static and runtime views cannot explain readiness without a persisted global graph.

When a trigger occurs, the existing execution-model design and reference reducer remain
the starting point. This proposal does not invent a competing scheduler model.

## Design

### Authored surface

The authored fields already exist, but applying `for_each` to a composite and projecting
the child’s declared ports are new compiler/runtime semantics:

```yaml
steps:
  - id: promote
    mode: code
    outputs:
      depth_roster:
        path: "{{run.dir}}/promotion/depth-roster.md"

  - id: mapped-work
    mode: composite
    uses: deps.item_process
    inputs:
      roster:
        ref: promote.depth_roster
    for_each:
      over: roster
      bind: item
      bind_fields: [item, label, input_artifact_path]
      key: "{{item}}"
    with:
      item: "{{item}}"
      label: "{{label}}"
      input_artifact_path: "{{input_artifact_path}}"

  - id: reduce
    mode: code
    inputs:
      outcomes:
        collect: mapped-work
        require: finished
        path: "{{run.dir}}/reduce/outcomes.yaml"
```

`for_each` retains its current roster, binding, item-key, and outcome meanings.
An upstream step output is mapped through a declared step input, as shown by `roster`.
Input resolution already derives the producer dependency and concrete path, while item
discovery remains lazy until the file exists.
The first slice does not add a second `for_each.over` expression language for direct
step-output references.

A composite scope does not use `max_concurrency` as a memory calculation: starting the
scope is cheap, and its executable leaves wait on the shared run/host authorities.
`for_each.max_concurrency` remains the optional ceiling on active scope evaluators; it
does not reserve leaf capacity or claim that the host can safely run that many agents.
Omitted, all materialized scopes may wait cheaply while the shared leaf authorities
admit executable work.

Whole-scope retries are not introduced in the first slice.
Child leaves use their existing retry policies.
Resuming a failed mapped item re-enters its child state and runs only incomplete work.
Before an item is skipped as complete, the runtime revalidates its projected child
outputs. The planner rejects a composite-specific `for_each.retry` until whole-scope
retry semantics are separately specified.

The operator may force one mapped item without touching siblings.
Qualified task selectors use `<step>/<item-key>` for the whole item and
`<step>/<item-key>::<child-step>` for a child step.
The existing global `--force` behavior remains available.
The exact CLI option for qualified selectors must preserve backward compatibility, but
manual state edits and out-of-band child `run-process` commands are not supported
recovery paths.

### Scope identity and run layout

For mapped step `breadth` and key `DELL`, the child root is:

```text
<run>/breadth/DELL/
```

Child steps remain below that root exactly as they are for an ordinary composite.
The mapped parent task uses the existing item state address:

```text
<run>/.state/tasks/breadth/DELL/
```

Qualified node strings use `/` for the item segment and retain `::` for composite
descent: the item is `breadth/DELL`, and its child step is `breadth/DELL::analyze`. Step
and item identities remain typed separately in task records.
Existing path-safe step and item keys cannot contain `/`, so the rendering convention
prevents the current item/composite `::` collision.

The parent task succeeds only after the child process terminates successfully and its
declared process outputs validate.
Its new result record projects the child’s public outputs.
The fan-in outcome schema is extended with those resolved output paths and a
child-evidence pointer; consumers never reconstruct child paths by convention.
A child failure remains inspectable at the child task that produced it; the parent
outcome links to that evidence rather than reducing it to exception text.

The documented outcome format retains `key`, `state`, `succeeded`, `error`, and
`output_failures`; adds an `outputs` map from logical port name to run-relative path;
and adds an `evidence` pointer to the child process or failing child task record.
New fields are additive for existing `collect:` readers.

The runtime keeps three completion views consistent for every item: mapped-parent task
state, child `process-status.yaml`, and child task state.
A crash between writes must recover to one explainable result, and a forced or resumed
item must not be considered complete until all three views and declared outputs agree.

### Named process ports

Use process contracts as the authored vocabulary instead of adding a second composite
I/O language. The compiler and runtime work needed to make them true composite ports is
part of this feature:

- item fields and `with:` bind scalar child process inputs;
- a composite step’s declared `inputs` bind same-named child process inputs after normal
  `ref:` resolution;
- the child process’s declared `outputs` are the composite step’s public outputs; and
- consumers use the ordinary `ref: <composite-step>.<output-name>` syntax.

Generic process-spec loading moves below `commands/` so the planner can load child specs
without reversing the engine/command dependency.
The planner verifies every binding, resolves public output paths beneath the item scope,
and rejects missing, ambiguous, or incompatible ports.
Each resolved port becomes an ordinary dependency clause plus a persisted binding, the
same intermediate form a future ready-task scheduler can consume.

Child variables contain scope-local framework built-ins, child defaults/dependencies,
mapped item fields, and explicitly declared bindings only.
The current `dict(parent_variables)` namespace leak is removed.
The runtime validates child process outputs at the child boundary before publishing the
parent result and again before accepting resume reuse.

Aliases and output renaming are deferred.
If a real collision appears, add one small alias surface backed by the same process
ports; do not introduce generic expression bindings in advance.

### Shared execution context

Replace the expanding list of recursive execution arguments with one internal
`RunExecutionContext`. It is an immutable container of shared references and framework
policy that must be identical through the run:

- backend and placement settings;
- run and operator concurrency ceilings;
- execution-profile registries and overrides;
- auth-pool flags and dispatch configuration for pool and scalar agents;
- admission posture and the shared leaf-admission authority;
- force selectors, skip policy, and continue-on-error policy;
- cancellation/fatal-error signal;
- a run-owned executor with an explicit worker ceiling for synchronous handlers and
  command supervision; and
- references to run-wide event and observation sinks where appropriate.

Process variables, local plans, scope paths, and child run directories remain explicit
arguments because they vary by scope.
The context is internal; it is not a new public service, singleton, or database.

The parent creates it once.
Recursive composite calls reuse it.
This closes the current gap where each `_orchestrate()` call creates its own global
semaphore and makes it hard to prove that an operator ceiling applies across sibling
scopes. It also removes the dead composite `external_semaphore` seam and the current
hard-coded child `skip_steps=set()` and `force=False` behavior.

Both synchronous handlers and command-backed code steps run off the event loop.
Tests must prove that a slow command cannot stop sibling scopes, RunPool supervision, or
run heartbeats, and that configured concurrency is not silently replaced by
`asyncio.to_thread`’s default executor limit.

### Resource admission

RunPool remains the executor for real fan-out subprocesses.
Scalar child agent steps retain their direct adapter path.
Command-backed code steps retain their direct subprocess path.
Every actual child-process launch from these paths must, however, acquire capacity from
one host authority. A count-only gate in front of independently adaptive pools is
insufficient: each pool otherwise sizes the same host as if it were alone.

The authority is a small filesystem protocol in the existing host namespace, not a
daemon or executor. A decision mutex protects a claims ledger.
Under that mutex, a caller reaps stale claims, samples current capacity, sums active
byte claims, and either records a new lease or waits.
Reusing the existing slot layout, `mkdir_lock` primitive, execution-profile hints, and
RunPool memory/process-tree utilities keeps the mechanism small while acknowledging that
atomic byte decisions are new coordination work.

Each claim uses:

- conservative `estimated_process_rss_bytes` for the selected harness/profile;
- current platform headroom and pressure state;
- configured host reserve and operator count ceiling;
- active leases and their outstanding estimated claims; and
- the launched child PID, creation time, and observed process-tree footprint.

No pool or scalar attempt starts before its claim is accepted under `required` posture.
After launch, the lease records the child identity and remains held until the supervised
process tree exits. Rising pressure stops new admissions; completed work releases
capacity. A high-memory host therefore admits more tasks up to the operator/provider
ceilings without changing the process spec.

RunPool may still calculate a desired concurrency and ramp it gradually, but desired
capacity never bypasses launch admission.
Every ramped launch re-consults the shared byte authority.
Warm scale state is advisory only: the pool takes a fresh pressure/headroom reading and
caps restored state through the same authority before launching.
Cold-ramp and warm-restore tests characterize the downstream crash fix that motivated
this rule.

Legacy processes retain the released best-effort posture by default.
A run may select `required` admission, and the motivating consumer must do so: timeout
or an unavailable authority leaves the task in an inspectable admission-wait/failure
state and never launches it unguverned.
The posture is run policy, not a domain step field.

Provider/account quota remains with the existing credential-pool and adapter machinery.
Do not build a generic vector-claim or budget ledger in this slice.
Execution profiles already distinguish Pi, Claude, Gemini, provider, model, and resource
hints, but their current default RSS estimates are mostly uniform.
M3 measures each harness process tree and feeds conservative values back into the
profiles before M4.

### Deployment boundary

The first mapped-scope slice has one writer and one execution host.
That host may be a laptop or a high-memory GCE VM; the same process spec adapts through
the host authority. It does not partition mapped composite items across `gcp-worker`
jobs.
That partitioning currently exists only in the agent fan-out executor, so the first
implementation must reject that combination with an actionable message rather than
silently run with a different topology.
A later multi-host slice can add mapped-scope partitioning when production evidence
justifies it.

`gcp-worker` is already a constrained multi-writer path.
The scheduler escalation test therefore concerns expanding or generalizing that
topology, not inventing multi-writer execution from scratch.

### Plan, status, and artifacts

The current recursive `PlanBundle`, qualified node IDs, progress scanner, and
Metabrowser projection are the base.
Extend them rather than creating a parallel graph format:

- source views show the composite child once, annotated with its mapping source and
  named ports;
- runtime views show one scope instance per materialized item key;
- status links a mapped parent outcome to failed or incomplete child leaves;
- trace follows declared inputs and outputs through composite ports; and
- an artifact view is rebuilt from process declarations plus accepted task results.

Metaproc already has a checked-in `docs/artifact-catalog.md` describing framework-owned
file kinds. Do not overload that name for run lineage.
Call any cached runtime view an artifact **index** or **lineage projection**, and make
it deletable/rebuildable.

For the first single-writer implementation, exact process/spec hashes and the existing
run configuration are sufficient for resume and comparison manifests.
Persisting a fully compiled recursive task graph is deferred to the general scheduler
unless the runtime-view tests show that source snapshots and recorded hashes cannot
reconstruct the run faithfully.

### Durability and pull request 31

Pull request 31 adds append-only attempt history, exact retry replay, crash-safe
terminal projection, and stronger task identity.
It must merge before any runtime branch for this proposal.
Its `scope_path` and attempt layout are prerequisite-shaped, and it changes the same
orchestration and state modules.
This proposal consumes those records but does not make the unimplemented remainder of
the full commit model—attempt-private staging, generations, fences, or distributed
publication—a prerequisite for one local writer.

No mapped-scope code may weaken pull request 31’s invariant: an admission wait is not an
execution attempt, and a child launch produces the same attempt facts as the equivalent
scalar launch.

## Implementation Plan

### Phase 0: Architecture fixture and proof

- [x] Merge pull request 31 and base every runtime slice on the resulting main branch.
- [ ] Add a tiny nested process fixture with two code leaves, one agent-shaped fake
  leaf, declared process inputs/outputs, three roster items, and one controlled failure.
- [x] Characterize recursive argument and semaphore ownership, force, root skip,
  continue policies, and synchronous command behavior before changing them.
- [ ] Complete the remaining cancellation, output-validation, result/state, resume, and
  fan-in characterization.
  Pull request 34 covers scalar credential behavior with a real subprocess and
  selected-label fallback.
- [x] Complete the first deep architecture review and incorporate every finding in the
  proposal and beads. Keep the proposal in draft until the amended sequencing is
  reviewed.
- [ ] Do not merge mapped execution before Phase 1 is complete.

### Phase 1: Shared recursive execution context

- [x] Introduce `RunExecutionContext` without changing authored specs and make every
  recursive evaluator reuse one run semaphore, cancellation signal, and policy bundle.
- [x] Propagate force, root-scoped skip, both continue policies, backend, profiles, and
  admission posture consistently through recursive scopes.
- [x] Pass auth-pool flags and dispatch configuration to scalar agent steps; assert the
  actual credential-pool label used by a child invocation.
- [ ] Converge scalar and fan-out pool exhaustion on the existing typed
  `fail-fast|wait|signal` retry-later policy and checkpoint machinery.
- [x] Run synchronous handlers and command-backed code steps off the event loop through
  a run-owned executor sized to the operator ceiling when one is configured.
- [ ] Prove a slow command does not block sibling work, RunPool supervision,
  cancellation, or heartbeats.
- [x] Remove the dead composite `external_semaphore` parameter and prove the run-wide
  executable-leaf ceiling across recursive siblings.

### Phase 2: Mapped scopes, ports, state, and recovery

- [ ] Move generic child-spec loading below `commands/`, then resolve named composite
  ports into ordinary dependency clauses and persisted bindings.
- [ ] Remove the planner rejection for `mode: composite` plus `for_each` after adding
  mode-specific port, topology, and retry validation.
- [ ] Call neutral `run_fan_out` with a composite invoker; do not add another gather
  loop or duplicate discovery/key/retry machinery.
- [ ] Execute each child under `<run>/<step>/<item-key>/` with scoped variables and the
  Phase 1 context.
- [ ] Persist mapped-parent running/completed/failed state and a result containing
  resolved outputs and a child-evidence pointer.
- [ ] Validate child outputs at the boundary and before resume reuse; enrich fan-in
  outcomes without requiring path reconstruction.
- [ ] Adopt `/` item segments and retain `::` composite descent across plan, status,
  resource, trace, and visualization IDs.
- [ ] Support qualified per-item and child-step force, propagate it through the child
  walk, and test consistency across parent task, child process, and child task views.
- [ ] Add cancellation, mixed-success, duplicate-key, invalid-port, namespace-isolation,
  path-containment, crash-window, and resume tests.
- [ ] Reject `gcp-worker` mapped-composite partitioning until a multi-host slice exists.

### Phase 3: One host byte authority

- [ ] Add a decision mutex and active-claims ledger to the existing host namespace;
  reconcile stale claims and admit atomically over fresh headroom, reserve, claims, and
  count ceilings.
- [ ] Make every RunPool, scalar-agent, and command-subprocess launch use that authority
  while retaining its existing execution path.
- [ ] Extend claims with profile, estimated bytes, child identity, and observed process
  tree by reusing RunPool platform utilities.
- [ ] Make cold ramp and warm-state restoration re-sample and obey the same byte
  authority before each launch.
- [ ] Add required admission posture while preserving the legacy default outside
  opted-in workflows.
- [ ] Measure Pi, Claude, and Gemini process trees and update conservative profile
  claims before high-concurrency testing.

### Phase 4: Existing-view integration

- [ ] Render mapped source scopes and runtime item instances through `PlanBundle` and
  the existing visualization model.
- [ ] Extend status and trace to link parent scope outcomes, child leaves, and declared
  artifacts.
- [ ] Add a rebuildable run artifact-lineage projection only if those views cannot
  answer producer, consumer, contract, path, and outcome directly.
- [ ] Document the runtime layout in the core architecture, operator reference, and
  artifact catalog without creating a second authority.

### Phase 5: Production proof and escalation decision

- [ ] Run nested fixture, failure/retry/resume, and concurrent-run admission suites.
- [ ] Run the same cohort fixture on constrained and high-memory Linux hosts and verify
  that safe concurrency changes without a spec edit.
- [ ] Run one real Pi, Claude, and Gemini smoke with the same mapped process.
- [ ] Measure duplicate write-boundary repository snapshots during M0-M3, then batch or
  cache the two per-attempt `git status` calls before M4 while preserving write-boundary
  detection tests.
- [ ] Measure barrier-drain idle as a fraction of wall-clock in M4 and M5.
- [ ] Complete the downstream cohort ladder owned by the consumer repository.
- [ ] Record whether any general-scheduler escalation trigger was observed.
  Open the scheduler implementation only with that evidence.

## Testing Strategy

### Deterministic tests

- Planner golden tests for mapped composite shape, named ports, qualified IDs, and
  invalid bindings, including a regression that item `/` and composite `::` segments do
  not collide.
- Runtime fixture tests for three items with success, contract failure, child exception,
  cancellation, per-item force, child-step force, output deletion, and resume.
- Exact assertions that no child CLI is started, no child orchestrator lease exists, and
  a scope holds no host slot while waiting on its children.
- Characterization tests for recursive semaphore, force, skip, continue, cancellation,
  auth, and output-validation behavior before and after `RunExecutionContext`.
- Event-loop tests in which slow command-backed code work runs concurrently with sibling
  scopes, pool supervision, cancellation, and heartbeats under the configured executor
  ceiling.
- Parent-state tests covering running/completed/failed transitions, child output
  validation, output/evidence projection, crash windows, and consistency among parent
  task, child process, and child task views.
- Fan-in tests for `require: succeeded` and `require: finished` over mapped composite
  outcomes, resolved output paths, and child-evidence pointers.
- Credential-pool tests that assert scalar child agents use the requested pool label,
  not ambient credentials.
- Retry-later tests for scalar and fan-out exhaustion under fail-fast, bounded wait, and
  signal/checkpoint policy, with admission waits excluded from attempt history.
- Compatibility tests proving all existing composite, agent fan-out, code fan-out,
  aligned-code-chain, and old-run readers behave unchanged.
- Admission tests with concurrent pool and scalar claim races, fake headroom and
  pressure signals, stale leases, unavailable namespaces, required versus best-effort
  posture, cold ramp, and warm-state restoration after a fresh reading.
- Process-tree tests on Linux PSS and macOS physical footprint where the platform
  exposes them.

### Integration and scale tests

Run an ordered cohort ladder rather than jumping directly to a report-day run:

| Rung | Workload | Required evidence |
| --- | --- | --- |
| M0 | Network-free three-item nested fixture | Exact graph, ports, parent/child state, outcomes, force, retry, resume, and artifact trace |
| M1 | One real harness, one item | Child stages run in-process with inherited policy, asserted credential-pool label, and no domain launcher |
| M2 | One harness, three items | Mixed outcome isolation, closed fan-in, output revalidation, and one-item repair |
| M3 | Pi, Claude, and Gemini, ten items | Profile/auth propagation, measured per-harness process-tree RSS fed back into profiles, provider behavior, and responsive event loop |
| M4 | Same 32-item spec on constrained and high-memory Linux hosts | Higher safe throughput on the larger host, no unadmitted launch, cold/warm safety, bounded pressure/stalls, write-boundary cost gate, and barrier-drain fraction |
| M5 | Full downstream shadow cohort | Eligible retained-baseline comparison, successful item repair/resume drill, and barrier-drain fraction |

Measure actual agent-tree memory, admission wait, active concurrency, throughput,
provider waits, retry classes, artifact coverage, write-boundary snapshot time, and
barrier-drain idle. For each barrier, record the interval from the first ready item to
roster closure divided by total run wall-clock; retain per-item wait data for diagnosis.
A completed run alone does not prove adaptive capacity.

## Current Validation Status

As of 2026-08-23, this proposal has been checked against the document and pull request
stack listed above, the production composite evaluator, fan-out paths, execution-profile
models, host admission, RunPool, and recursive visualization code.
The pull request 32 deep review independently verified the load-bearing runtime claims,
approved the mapped-scope primitive, and required the sequencing, state/evidence,
resource, and recovery corrections recorded above.

Pull request 31 is merged, and
[pull request 33](https://github.com/jlevy/metaproc/pull/33) publishes the first runtime
slice above this design.
It introduces one recursive execution context, shared local leaf admission, off-loop
synchronous execution, and explicit root-versus-child force, skip, and continue
semantics. [Pull request 34](https://github.com/jlevy/metaproc/pull/34) is stacked on
pull request 33 and completes scalar credential-pool propagation, including scoped child
evidence, fallback-label retry, shared fan-out/scalar completion primitives, and
classification-before-compaction ordering.
Its local, pre-push, and five-job CI verification passes formatting, Ruff, BasedPyright,
Markdown links, public hygiene, supply-chain and browser checks, dependency audits,
distribution build and smoke tests, plus 4,275 tests with 8 skipped.
The first Python 3.14 CI sample tripped the existing readiness-scale timing ratio at
6.4× against its 6.0× bound; the full 3.12/3.13/3.14 rerun passed.
`mp-npza` tracks a statistically stable replacement that preserves the complexity
regression gate instead of weakening it by threshold alone.

End-to-end cancellation proof, unified retry-later dispatch, the mapped-scope fixture,
mapped execution, shared byte admission, and production-scale results remain open.
The first F1–F8 architecture-review disposition is complete.
The proposal remains a draft for review while implementation proceeds as independently
reviewable stacked slices.

## Rollout Plan

1. Merge pull request 31.
2. Merge this plan through pull request 32.
3. Merge the `RunExecutionContext` and nonblocking-execution slice through pull request
   33, which is based on pull request 32.
4. Merge scalar credential propagation through pull request 34, which is based on pull
   request 33; then finish cancellation-safe ownership and unified retry-later policy as
   separately reviewable stacks.
5. Land Phase 2 mapped scopes, ports, parent state/evidence, and per-item recovery.
6. Land Phase 3 shared host byte authority before a mapped workflow is production-ready.
7. Integrate the existing views and complete the M0-M4 framework ladder.
8. Run the downstream workflow only as a shadow consumer until its comparison ladder
   passes.
9. Keep the full scheduler beads deferred unless an escalation trigger is recorded.

Every runtime pull request must be independently revertible.
Existing specs continue to use their released paths throughout rollout.

## Open Question

- Does `run-config.yaml` plus exact source/spec hashes reconstruct runtime views well
  enough after hydration, or does the run need a compact snapshot of the recursive
  process bundle? Add the snapshot only if the hydration test fails.

## Tracking

`mp-0iy8` owns this proposal.
`mp-p0sn` is the pull request 31 merge gate; `mp-zssw` owns shared recursive policy and
nonblocking execution.
Its first-slice beads are `mp-htd8` (characterization), `mp-vf21` (shared context and
leaf ceiling), `mp-d12o` (run-owned executor), and `mp-bvjd` (scalar-auth policy), all
complete, plus `mp-l6b5` (remaining cancellation proof) and `mp-tibt` (unified
retry-later dispatch), both open.
`mp-npza` tracks the non-blocking stabilization of the noisy execution-model scale
timing gate observed during pull requests 32 and 34. `mp-0ukj` owns mapped scopes,
ports, parent evidence, and within-scope per-item recovery; `mp-0cyw` owns the common
host byte authority; `mp-1af0` owns views; and `mp-rrfn` owns the production proof.

The general ready scheduler, persisted dynamic expansions, complete fenced publication,
cross-scope causal force, budgets, and a standalone runtime artifact index remain
evidence-triggered follow-ons rather than cohort prerequisites.

## References

- [Metaproc concepts and principles](../../../../src/metaproc/docs/metaproc-concepts-and-principles.md)
- [Process framework concepts](../../../process-framework-concepts.md)
- [Core architecture](../../../arch/arch-metaproc-core.md)
- [Execution-model design](../../../execution-model-design.md)
- [Execution-model architecture](../../../arch/arch-execution-model.md)
- [RunPool architecture](../../../arch/arch-runpool.md)
- [Cloud execution architecture](../../../arch/arch-cloud-execution.md)
- [Authentication and credential pools](../../../arch/arch-authentication.md)
- [File I/O utilities](../../../arch/arch-file-io-utilities.md)
- [Testing architecture](../../../arch/arch-testing.md)
- [Conventions](../../../conventions.md)
- [Artifact catalog](../../../artifact-catalog.md)
- [Performance notes](../../../performance-notes.md)
- [Revision 3 proposals, especially P8](../../../metaproc-design-rev3-proposals.md)
- [Contract-failure primitives plan](./plan-2026-08-20-contract-failure-primitives.md)
- [Metaproc pull request 21](https://github.com/jlevy/metaproc/pull/21)
- [Metaproc pull request 29](https://github.com/jlevy/metaproc/pull/29)
- [Metaproc pull request 31](https://github.com/jlevy/metaproc/pull/31)
- [Metaproc pull request 32](https://github.com/jlevy/metaproc/pull/32)
- [Metaproc pull request 33](https://github.com/jlevy/metaproc/pull/33)
- [Metaproc pull request 34](https://github.com/jlevy/metaproc/pull/34)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
