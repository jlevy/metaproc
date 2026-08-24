---
title: Native Mapped Composite Scopes
description: >-
  Extend existing Metaproc composition and admission primitives just enough to run a
  process once per roster item inside one framework-owned run, without a child CLI or a
  new general scheduler.
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

This proposal adds a smaller primitive: an existing composite may be mapped over an
existing roster. Each item becomes an in-process child scope beneath the parent run.
The child uses the current recursive process evaluator, current task records, current
agent and code executors, and current process input/output declarations.
One internal execution context carries run-wide policy and admission through every
recursive call.

The proposal does **not** make the general ready-task scheduler in
[`execution-model-design.md`](../../../execution-model-design.md) a prerequisite.
The motivating workflow can express its cohort barriers explicitly—breadth, promotion,
depth, and review—and let each mapped item process use the established level walk
internally. The full task scheduler remains a valid later design if a workload
demonstrates the need for cross-scope streaming, changing expansion generations, or
multi-writer scheduling.

## Decision Summary

Implement the smallest coherent slice:

1. permit `for_each` on a composite step;
2. execute every mapped child scope through the existing in-process composite path;
3. carry one internal execution context through the recursive run;
4. use the child process’s declared inputs and outputs as the composite’s named ports;
5. extend the existing host-admission lease with required, memory-aware scalar-agent
   admission; and
6. extend existing plan, status, trace, and Metabrowser projections to show mapped
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
- Requiring attempt-private publication, fenced commits, per-item force, causal
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

| Concern | Existing Metaproc primitive | Narrow change |
| --- | --- | --- |
| Process composition | `mode: composite` loads a child spec and calls `_orchestrate()` in-process beneath the parent run | Apply the same path once per roster item |
| Mapping | `for_each` discovery, item keys, per-item state, retry, and fan-in outcomes exist for agent/code steps | Reuse the neutral mapping pieces for composite scopes |
| Source visualization | `PlanBundle` and the viz projector recurse through composite children with qualified node IDs | Add the mapping declaration and runtime item instances |
| Artifact ports | Process inputs and process outputs, including output re-exports, already exist | Bind composite inputs by logical name and expose declared child outputs |
| Execution policy | Parent backend, profile files, variant, auth-pool settings, and cloud settings already propagate into scalar composites | Carry them in one internal context instead of a growing argument list |
| Fan-out resources | RunPool owns adaptive subprocess concurrency and host admission | Keep it; do not build a scheduler around it |
| Scalar resources | Scalar agents take the same host slot namespace, but the gate is count-only and best-effort | Add current-headroom claims, child identity, and a required posture |
| Failure feedback | Structured output failures and bounded corrective prompts are merged on main through pull request 29 | Reuse unchanged inside mapped children |
| Task evidence | Current status, attempt, result, process events, and fan-in outcomes are durable | Nest them under the mapped scope key; pull request 31 may add append-only history independently |

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
- pull request 31 is an independent attempt-history/replay slice and does not implement
  mapped scopes, output staging, generations, or the ready scheduler.

The consequence is important: this proposal may reuse and extend shipped primitives, but
it may not describe a reference-model feature as already implemented or revive an older
proposal under a new name.

## Architecture Review

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
- scope evaluation does not itself acquire an agent slot; and
- cancellation and fatal run policy flow downward from the parent.

Child scopes retain nested state and event directories because those are useful
addresses, not separate scheduling authorities.

### Escalation triggers for the general scheduler

Do not implement the production ready-task scheduler merely because the reference model
can describe it. Revisit it when at least one production workload requires one of these
properties and cannot be expressed cleanly with mapped scopes and explicit barriers:

- a fork or join must stream item-by-item across sibling mapped steps;
- a downstream expansion must begin before an upstream roster is closed;
- tasks from several mapped scopes need global fairness beyond host/provider admission;
- an operator needs causal per-item force across scope boundaries;
- more than one writer may schedule attempts for the same run; or
- static and runtime views cannot explain readiness without a persisted global graph.

When a trigger occurs, the existing execution-model design and reference reducer remain
the starting point. This proposal does not invent a competing scheduler model.

## Design

### Authored surface

The only new authored combination is already meaningful syntax:

```yaml
steps:
  - id: mapped-work
    mode: composite
    uses: deps.item_process
    for_each:
      over: deps.roster
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
A composite scope does not use `max_concurrency` as a memory calculation: starting the
scope is cheap, and its executable leaves wait on the shared run/host authorities.
`for_each.max_concurrency` remains the optional ceiling on active scope evaluators; it
does not reserve leaf capacity or claim that the host can safely run that many agents.
Omitted, all materialized scopes may wait cheaply while the shared leaf authorities
admit executable work.

Whole-scope retries are not introduced in the first slice.
Child leaves use their existing retry policies.
Resuming a failed mapped item re-enters its child state and runs only incomplete work.
The planner rejects a composite-specific `for_each.retry` until whole-scope retry
semantics are separately specified.

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

The parent task succeeds only after the child process terminates successfully and its
declared process outputs validate.
Its result projects the child’s public outputs, so the existing `collect:` fan-in record
can carry a structured outcome and output paths.
A child failure remains inspectable at the child task that produced it; the parent
outcome links to that evidence rather than reducing it to exception text.

### Named process ports

Use the process contracts that already exist instead of adding a second composite I/O
language:

- item fields and `with:` bind scalar child process inputs;
- a composite step’s declared `inputs` bind same-named child process inputs after normal
  `ref:` resolution;
- the child process’s declared `outputs` are the composite step’s public outputs; and
- consumers use the ordinary `ref: <composite-step>.<output-name>` syntax.

The planner recursively loads the child spec, verifies every binding, resolves its
public output paths beneath the item scope, and rejects missing, ambiguous, or
incompatible ports. The runtime validates child process outputs before publishing the
parent result. No consumer reconstructs a child path from directory conventions.

Aliases and output renaming are deferred.
If a real collision appears, add one small alias surface backed by the same process
ports; do not introduce generic expression bindings in advance.

### Shared execution context

Replace the expanding list of recursive execution arguments with one internal, immutable
`RunExecutionContext`. It carries only framework policy that must be identical through
the run:

- backend and placement settings;
- run and operator concurrency ceilings;
- execution-profile registries and overrides;
- auth-pool configuration;
- admission posture and the shared leaf-admission authority;
- cancellation/fatal-error signal; and
- references to run-wide event and observation sinks where appropriate.

Process variables, local plans, scope paths, and child run directories remain explicit
arguments because they vary by scope.
The context is internal; it is not a new public service, singleton, or database.

The parent creates it once.
Recursive composite calls reuse it.
This closes the current gap where each `_orchestrate()` call creates its own global
semaphore and makes it hard to prove that an operator ceiling applies across sibling
scopes.

### Resource admission

RunPool remains the executor for real fan-out subprocesses.
Scalar child agent steps continue to use their direct adapter path, but the existing
`HostAdmissionGate` becomes the common host authority for both paths.

This is narrower than either obvious alternative.
Constructing a one-item RunPool for every scalar leaf would create many pool lifecycles
and adaptive controllers, while one heterogeneous run-wide RunPool would require the
invoker/executor refactor this proposal defers.
The host gate is already the coordination seam shared by pools and scalar launches.
Adding an atomic byte claim and reusing RunPool’s platform memory/process-tree utilities
there extends one primitive instead of introducing another executor.

The minimal host claim uses fields already present in execution profiles and telemetry:

- conservative `estimated_process_rss_bytes` for the selected harness/profile;
- current platform headroom and pressure state;
- configured host reserve and operator count ceiling;
- active leases and their outstanding estimated claims; and
- the launched child PID, creation time, and observed process-tree footprint.

Admission is an atomic claim in the existing host namespace.
A new attempt starts only after the claim is accepted.
After launch, the lease records the child identity and remains held until the supervised
process tree exits. Rising pressure stops new admissions; completed work releases
capacity. A high-memory host therefore admits more tasks up to the operator/provider
ceilings without changing the process spec.

Legacy processes retain the released best-effort posture by default.
A run may select `required` admission, and the motivating consumer must do so: timeout
or an unavailable authority leaves the task in an inspectable admission-wait/failure
state and never launches it unguverned.
The posture is run policy, not a domain step field.

Provider/account quota remains with the existing credential-pool and adapter machinery.
Do not build a generic vector-claim or budget ledger in this slice.
Execution profiles already distinguish Pi, Claude, Gemini, provider, model, and resource
hints; the tests should first show which additional authority, if any, production
requires.

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
It is useful independent groundwork and may land before this work.
This proposal consumes those records when present but does not make the unimplemented
remainder of the full commit model—attempt-private staging, generations, fences, or
distributed publication—a prerequisite for one local writer.

No mapped-scope code may weaken pull request 31’s invariant: an admission wait is not an
execution attempt, and a child launch produces the same attempt facts as the equivalent
scalar launch.

## Implementation Plan

### Phase 0: Architecture fixture and proof

- [ ] Add a tiny nested process fixture with two code leaves, one agent-shaped fake
  leaf, declared process inputs/outputs, three roster items, and one controlled failure.
- [ ] Characterize current scalar composite execution, recursive plan rendering,
  process-output validation, resume, and fan-in outcomes.
- [ ] Implement a narrow spike of mapped in-process composite execution without changing
  task persistence or admission.
- [ ] Compare the code and state delta against a static-flattening spike.
  Stop if the mapped implementation requires a second lease, CLI, or durable scheduler.
- [ ] Complete architecture review on this document before merging runtime code.

### Phase 1: Mapped scopes and named ports

- [ ] Remove the planner rejection for `mode: composite` plus `for_each` after adding
  the mode-specific validation described above.
- [ ] Reuse item discovery, binding, key validation, state paths, and fan-in outcome
  generation for mapped composite tasks.
- [ ] Execute each child scope under `<run>/<step>/<item-key>/` through the existing
  recursive composite path.
- [ ] Bind same-named inputs and project/validate child process outputs.
- [ ] Preserve structured child failure evidence and aggregate it in the mapped parent
  outcome.
- [ ] Add resume, cancellation, mixed-success, duplicate-key, invalid-port, and
  path-containment tests.

### Phase 2: Shared context and adaptive scalar admission

- [ ] Introduce the internal `RunExecutionContext` and pass it through recursive
  orchestration without changing existing authored specs.
- [ ] Make the run-wide operator ceiling apply to executable leaves across all mapped
  scopes.
- [ ] Extend host leases with estimated bytes, profile identity, child identity, and
  observed process-tree footprint by reusing RunPool memory utilities.
- [ ] Make admission decisions atomic over current headroom, reserve, active claims, and
  the operator ceiling.
- [ ] Add required admission posture while preserving the legacy default.
- [ ] Prove Pi, Claude, and Gemini profile propagation and profile-specific claims.

### Phase 3: Existing-view integration

- [ ] Render mapped source scopes and runtime item instances through `PlanBundle` and
  the existing visualization model.
- [ ] Extend status and trace to link parent scope outcomes, child leaves, and declared
  artifacts.
- [ ] Add a rebuildable run artifact-lineage projection only if those views cannot
  answer producer, consumer, contract, path, and outcome directly.
- [ ] Document the runtime layout in the core architecture, operator reference, and
  artifact catalog without creating a second authority.

### Phase 4: Production proof and escalation decision

- [ ] Run nested fixture, failure/retry/resume, and concurrent-run admission suites.
- [ ] Run the same cohort fixture on constrained and high-memory Linux hosts and verify
  that safe concurrency changes without a spec edit.
- [ ] Run one real Pi, Claude, and Gemini smoke with the same mapped process.
- [ ] Complete the downstream cohort ladder owned by the consumer repository.
- [ ] Record whether any general-scheduler escalation trigger was observed.
  Open the scheduler implementation only with that evidence.

## Testing Strategy

### Deterministic tests

- Planner golden tests for mapped composite shape, named ports, qualified IDs, and
  invalid bindings.
- Runtime fixture tests for three items with success, contract failure, child exception,
  cancellation, and resume.
- Exact assertions that no child CLI is started, no child orchestrator lease exists, and
  a scope holds no host slot while waiting on its children.
- Fan-in tests for `require: succeeded` and `require: finished` over mapped composite
  outcomes.
- Compatibility tests proving all existing composite, agent fan-out, code fan-out,
  aligned-code-chain, and old-run readers behave unchanged.
- Admission tests with fake headroom and pressure signals, simultaneous claim races,
  stale leases, unavailable namespaces, and required versus best-effort posture.
- Process-tree tests on Linux PSS and macOS physical footprint where the platform
  exposes them.

### Integration and scale tests

Run an ordered cohort ladder rather than jumping directly to a report-day run:

| Rung | Workload | Required evidence |
| --- | --- | --- |
| M0 | Network-free three-item nested fixture | Exact graph, ports, outcomes, retry, resume, and artifact trace |
| M1 | One real harness, one item | Child stages run in-process with inherited policy and no domain launcher |
| M2 | One harness, three items | Mixed outcome isolation and closed fan-in |
| M3 | Pi, Claude, and Gemini, ten items | Profile propagation, provider behavior, and resource evidence |
| M4 | Same 32-item spec on constrained and high-memory Linux hosts | Higher safe throughput on the larger host, no unadmitted launch, bounded pressure/stalls |
| M5 | Full downstream shadow cohort | Eligible retained-baseline comparison and successful resume drill |

Measure actual agent-tree memory, admission wait, active concurrency, throughput,
provider waits, retry classes, and artifact coverage.
A completed run alone does not prove adaptive capacity.

## Current Validation Status

As of 2026-08-23, this proposal has been checked against the document and pull request
stack listed above, the production composite evaluator, fan-out paths, execution-profile
models, host admission, RunPool, and recursive visualization code.
Markdown formatting, local-link checks, public-hygiene checks, supply-chain checks, and
the repository lint suite pass.

No runtime implementation or production-scale result is claimed.
The architecture-review bead remains in progress until reviewers accept or amend the
Phase 0 decision; all implementation checkboxes remain open.

## Rollout Plan

1. Merge this plan as a draft architecture proposal.
2. Land the Phase 0 fixture and implementation spike in a reviewable pull request.
3. Merge mapped scopes and named ports without enabling required admission by default.
4. Land adaptive scalar admission and the shared context in a separate pull request.
5. Run the downstream workflow only as a shadow consumer until its comparison ladder
   passes.
6. Keep the full scheduler beads deferred unless an escalation trigger is recorded.

Every runtime pull request must be independently revertible.
Existing specs continue to use their released paths throughout rollout.

## Open Questions

- Can the existing host-admission namespace make a byte claim atomic with a small
  decision lease, or should byte admission be a process-local authority backed by the
  cross-run count gate?
  Choose after a race-focused spike; do not introduce a daemon.
- Does `run-config.yaml` plus exact source/spec hashes reconstruct runtime views well
  enough after hydration, or does the run need a compact snapshot of the recursive
  process bundle? Add the snapshot only if the hydration test fails.
- Which portions of pull request 31 should merge before mapped scopes for review
  clarity, even though the full generation/commit protocol is not a runtime gate?

## Tracking

The existing `mp-0iy8` execution-model epic will be narrowed to this proposal.
Mapped scope execution, adaptive scalar admission, views, and production proof remain
P1. The general ready scheduler, persisted dynamic expansions, complete fenced
publication, per-item causal force, budgets, and a standalone runtime artifact index
become evidence-triggered follow-ons rather than cohort prerequisites.

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

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
