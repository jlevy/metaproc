# Step Modes and Task Invocation

A design note on why `mode` currently decides more than it should, what that costs, and the
smaller set of primitives that would replace it.
It follows [process-framework-concepts.md](process-framework-concepts.md), which supplies
the object model this note argues the runtime should actually honor.

## The Observation

Metaproc has four step modes, and `_execute_step` forks on all four. Each branch owns its
own execution path, so a capability lands in whichever branches someone implemented it in:

| Capability | agent | code | manual | composite |
| --- | --- | --- | --- | --- |
| Fan-out over a roster | yes | no | no | no |
| RunPool admission | fan-out path only | no | no | no |
| Per-item retry and backoff | yes | no | no | no |
| Per-item state and logs | yes | via path helpers | via path helpers | via path helpers |

The gaps are not deliberate policy. `for_each` is a statement about a step's relationship
to a roster, and nothing about that statement is agent-specific; a code step mapping over a
roster is as coherent as an agent step doing so. It simply was not written twice.

Measured, not inferred: a three-stage spec whose stages are `mode: code` with `for_each`
plans as a fan-out, reporting four items per level, and then executes each step exactly
once with no item bound, so the handler fails on a missing item variable.

## Why It Costs More Than the Missing Feature

**The consumer routes around it.** The GTIA v3.0 design specifies each stage as a
`mode: code` fan-out step whose handler makes one `metaproc run-process` call for its item's
child spec. That shape exists because `for_each` on `mode: composite` is not available, so a
composite mapped over a roster has to be smuggled in as a code handler that launches a child
run. The workaround is reasonable given the constraint and would be unnecessary without it.

**Invocation leaks into scheduling.** A code handler is a synchronous callable invoked
inline on the event loop, so a fan-out dispatcher that gathers its items still executes them
one at a time. On a four-item roster with dwell times of 2, 5, 2 and 8 seconds, the step took
17 seconds, the sum rather than the maximum, with zero overlap between items. That is not a
concurrency policy anyone chose; it is an invocation detail deciding a scheduling outcome.

**Governance is unreachable from three of four modes.** The concepts doc already records
this as a deviation: RunPool governs only the fan-out execution path, so a singly launched
step bypasses admission entirely. Stated in mode terms, admission is a property of one
branch rather than of tasks.

## The Smaller Set

The concepts doc names the pivot and the runtime does not yet honor it:

> **Task:** one step applied to one item... the task is the pivotal object in this model,
> because it is the correct unit of scheduling, of failure, and of resume.

Three concerns are fused into `mode` today, and they are independent:

1. **Mapping**, step to tasks: does this step produce one task or one per roster item? This
   is `for_each`, and it is a property of the step's relationship to data.
2. **Invocation**, how one task is performed: run an agent CLI, call a Python handler, run a
   shell command, wait for a person, run a child spec.
3. **Governance**, when and where a task may run: admission, concurrency ceiling, retry
   policy, placement.

Only the second varies by mode. The first and third are properties of tasks and should be
answered identically no matter what performs the work.

So the minimal decomposition is one interface and one rule.

**`Invoker.run(task) -> disposition`**, with one implementation per mode. `mode` selects an
invoker and decides nothing else.

**Everything else operates on tasks and never asks which invoker produced them.** Mapping
expands a step into tasks before any invoker is chosen. Admission grants a claim to a task.
Retry re-attempts a task. State, logs, and commits address a task. Dependency clauses relate
tasks.

## What That Buys

Each of these is a current gap that closes without being separately implemented:

- Code steps gain fan-out, because mapping happens before invoker selection.
- Composite steps gain fan-out, and the v3.0 per-item-invoker workaround stops being
  necessary: mapping a child spec over a roster becomes the composite invoker's ordinary
  behavior.
- Every mode gains admission, closing the deviation the concepts doc records as test 7.
- A per-step concurrency ceiling, currently absent, becomes one governance property on
  tasks rather than four parallel implementations.
- Blocking becomes an invoker contract. Either every invoker is awaitable, or the one place
  that calls them wraps synchronous ones off the loop, rather than each branch deciding.

The authored surface does not change. A spec still says `mode: code` and `for_each`, and
means what an author already expects it to mean. What changes is that the runtime stops
treating those two words as one decision.

## Scope of the Claim

This note argues a shape, not a migration. The four execution paths differ in real ways
beyond invocation, particularly the agent path's adapters, variants, execution profiles, and
auth pools, and collapsing them is not a mechanical refactor. What the evidence supports is
narrower and firmer: mapping and governance do not belong to `mode`, and every place they
are attached to it has produced either a missing capability or a consumer working around
one.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
