---
title: Execution Stability, Operator Diagnostics, and Flexibility
description: Make execution failures explainable to the operator, close the RunPool stability findings, and gate further execution flexibility with evidence.
author: Codex with maintainer review
date: 2026-09-10
status: In Review
tracking_bead: mp-7p3z
---
# Plan: Execution Stability, Operator Diagnostics, and Flexibility

**Date:** 2026-09-10 (last updated 2026-09-10)

**Status:** In review.
F1–F3 are implemented in [pull request 75](https://github.com/jlevy/metaproc/pull/75);
F4–F10 remain open.

## Overview

Epic `mp-7p3z` owns the remaining RunPool and execution work identified by the
[RunPool design review](https://github.com/jlevy/metaproc/pull/75#issuecomment-5621778931),
including the related open work carried forward from the
[completed mapped-scope plan](../done/plan-2026-08-25-consolidated-mapped-scope-runtime.md).
The completed release epic `mp-0iy8` retains its shipped scope and historical evidence.

The tbd graph owns current status, holds, and dependencies.
This plan owns the scope and acceptance criteria.
A review finding stays open until its implementation and acceptance evidence are
complete, or the maintainer records an explicit alternative disposition.
Creating a plan link or completing a documentation correction does not resolve a runtime
finding.

## Goals

- Surface actionable failures prominently to the operator agent, with causes and
  evidence rather than unexplained failure counts or exit codes
- Preserve task causes, process ownership, and durable execution evidence across errors
  and recovery
- Give each implementation one owner and each review finding a verifiable closure
  condition
- Keep larger scheduler, lifecycle, and retry-policy changes behind their existing
  evidence requirements

## Ownership

| Finding | Review bead | Implementation ownership | Governing plan |
| --- | --- | --- | --- |
| F4: run-level causes | `mp-4en5` | The same bead owns the bounded summary contract and its implementation. | This plan |
| F5: host-safety rollout | `mp-p71a` | `mp-3c0g` produces broker and owned-launch behavior; `mp-g3si` integrates it under the retained RunPool. `mp-bd6v` remains the package epic. | [Host safety](plan-2026-09-01-runpool-host-safety.md) and [Safeproc](plan-2026-09-01-safeproc-local-incubation.md) |
| F6: stale-slot replacement | `mp-zmpo` | This bead owns the Metaproc race reproducer and closure decision, coordinated with `mp-3c0g`, `mp-c225`, and `mp-g3si`. | [Host safety](plan-2026-09-01-runpool-host-safety.md) |
| F7: durable publication and budgets | `mp-bawb` | `mp-82ls` owns `mp-rfnm`, `mp-2wtc`, `mp-c5wt`, `mp-g315`, and `mp-vu2v`. | This plan |
| F8: admission waits and attempts | `mp-skoo` | `mp-ux0f` owns the correction. `mp-f5m5` is its duplicate; its exception-ordering and compatibility requirements are retained. | This plan |
| F9: empty-run completion | `mp-q8xq` | The same bead owns the compatibility decision and implementation; `mp-rrfn` supplies broader recovery evidence. | This plan |
| F10: run-wide `fail_run` | `mp-rrkw` | `mp-cl0d` owns implementation under contract-failure epic `mp-d019`. | This plan; [contract-failure design](../../design/contract-failure-primitives.md) |

F5, F7, F8, and F10 are acceptance gates for existing implementation work.
Their blocking edges point to the relevant implementation beads.
F6 remains actionable for a reproducer and rollout decision before the new broker is
available; coordination does not require blocking that investigation on a package
release.

## Operator Diagnostics: No Unexplained Failure

`mp-5les` owns the operator-facing report and documentation contract.
F4, F8, F9, and F10 own the underlying execution facts and behavior; the report must
preserve those facts through the final CLI output.
The generated operator skill routes the agent to Metaproc commands, so a command that
hides available failure evidence breaks that operating contract.

The current [status renderer](../../../../src/metaproc/commands/status.py) can omit
failed-item details already collected by the
[status scanner](../../../../src/metaproc/engine/run_status.py).
Partial failures may therefore appear only as counts, particularly while a run
continues. This is a concrete acceptance case for the new report, independent of the
broader scheduler work.

- [ ] Define one typed diagnostic projection for `status` text and JSON, terminal `wait`
  output, and `pulse`. Put an attention block immediately after the overall status,
  before progress and timing tables, whenever action or investigation is needed.
- [ ] Include severity/state, scope/step/item, originating stage, cause class, a short
  redacted explanation, attempt and retry state, and durable evidence references.
  Preserve the primary cause when shutdown, logging, or cleanup also fails.
- [ ] Show partial failures even when orchestration continues or ends as `completed`.
  Distinguish a recovered attempt from a terminal failure, scheduled retry from
  exhausted budget, cancellation from failure, and waiting from executing.
- [ ] Explain leaf, host, credential, quota, and adaptive-pressure waits with their
  elapsed time and waiting/refused/released disposition.
  Unknown admission reasons remain explicitly unknown.
  Rendering a wait must not create an execution attempt.
- [ ] Render an explicit code such as `cause_unavailable` when a failed or blocked
  outcome lacks a cause.
  Identify the missing, unreadable, or corrupt evidence and link the nearest available
  source. Do not infer a root cause from absence or silently omit a projection failure.
  Other available diagnostics must still render.
- [ ] Use logical run-relative artifact or event locators that remain valid after
  relocation or compression.
  Provide a supported command for expanded detail, using existing commands where
  possible; decide exact syntax during implementation.
- [ ] Apply one tested redaction policy before rendering or persisting new diagnostics.
  Keep safe error codes, HTTP status, adapter/model identity, and bounded excerpts while
  excluding credentials, secret URL parameters, and private prompt payloads.
- [ ] Bound the attention block deterministically: counts by class, representative
  instances in stable scope order, an omitted-instance count, and an expansion command.
  Large fan-outs must not flood the operator agent’s context.
- [ ] Add golden coverage for scalar, mapped, manual, and composite failures; retries
  that recover or exhaust; admission refusals; partial success; cancellation and abort;
  valid empty work; missing rosters; corrupt records; legacy runs; and redaction.
  Assert text and JSON carry the same causal facts.
  A bare failure count is insufficient.
- [ ] Update the operator manual and generated skill together.
  State how to read and expand diagnostics and when success cannot be established.
  Regenerate committed skill copies and verify the installed-wheel help surface.

No failing or blocked outcome may be presented as a generic black-box error when a more
specific cause is available.
When the cause cannot be recovered, the report must make that uncertainty and the
missing evidence visible.
This requirement does not promise to invent information an adapter never emitted.

### Direct access to original agent logs

`mp-83g2`, under `mp-5les`, owns direct log access throughout the process lifecycle.
Metaproc’s rollup is an entry point to the evidence.
Debugging agent behavior must also support reading the original stdout, stderr, and
native transcript without Metaproc parsing, filtering, classification, or trace
extraction.

Current logged local launches combine stderr with stdout.
The Pi capture filter drops native update events before writing the task log, so direct
file access alone cannot meet this contract.
The implementation must retain a raw stream before any filtering and label combined
captures honestly.

- [ ] Expose the source-log locations for every agent attempt, including successful
  attempts and earlier retries, while running and after completion, failure, or
  cancellation. Identify the scope, step, item, attempt, adapter, and provider session
  when available; never mix evidence from two attempts.
- [ ] Show direct evidence links beside actionable diagnostics and in normal attempt
  inspection. Offer a copyable path and a direct open or tail command, with a clear way
  to reach stderr, stdout, the native transcript, and the relevant time range.
  If streams are combined, expose one accurately labeled combined stream rather than
  claiming that the same file supplies independently captured stdout and stderr.
- [ ] Preserve a raw viewing path that shows original records and ordering.
  Parsed or summarized views must label themselves and link back to the source.
  Debugging must remain possible when a Metaproc parser fails or does not recognize a
  native event. Retain the raw capture before the Pi filter or any later compaction; test
  that events omitted from the summary remain present in the original evidence.
- [ ] Resolve locators through nested scopes, cloud workers, hydration, relocation, and
  gzip. If evidence is remote, pending upload, not downloaded, missing, or never
  captured, state that condition and the supported retrieval or capture step.
  Do not silently show an empty log as proof that nothing happened.
- [ ] Define retention and compaction so the original evidence remains accessible.
  Lossless compression may change storage; lossy summaries or compaction must not
  silently replace the only original transcript.
  Any retention expiry must follow an explicit policy and leave its unavailability
  visible. Disclose historical runs whose original payloads are already unavailable.
- [ ] Preserve authorized direct access to original artifacts while keeping routine
  reports and copied excerpts redacted.
  Do not rewrite original evidence to fit the rollup schema, and do not copy private
  payloads into public review records.
- [ ] Test source mapping across attempts and providers, live append, restart,
  compression, nested and hydrated runs, missing/corrupt metadata, and unrecognized
  native events. Test that the drilldown reaches source evidence rather than another
  derived Metaproc view.
- [ ] Make operator guidance encourage direct source inspection when debugging.
  An operator report must distinguish the observed cause from Metaproc’s classification,
  identify the evidence inspected, and state what remains unknown.
  Routine monitoring still uses the CLI’s state and lifecycle contracts.

## Implementation and Acceptance

- [ ] **F4 — `mp-4en5`:** define a typed step outcome with bounded failure-class and
  output-failure-kind counts plus durable source references.
  Reuse the existing fan-in collectors and expected rosters.
  Prove scalar, mapped, manual, and composite failures retain their causes in run
  summaries and process events, including items never reached.
  Review persisted reader compatibility before changing the summary shape.
- [ ] **F5 — `mp-p71a`:** verify one coherent host budget, startup reservations and
  pacing, fail-closed admission on timeout and I/O errors, and independent containment.
  Cover scalar and mapped routes, mixed client versions and limits, broker failure,
  parent death with a surviving child, blocked-parent behavior, and unrelated host
  pressure. Retain RunPool as the queue and adaptive controller.
- [ ] **F6 — `mp-zmpo`:** reproduce two reclaimers observing one stale lease, followed
  by one claimant replacing it before the other deletion.
  Serialize reclaim and reserve with identity or epoch fencing.
  Prove no fresh lease is removed and capacity cannot be over-admitted.
  Validate macOS and Linux behavior and the old-client rollout; a lock observed only by
  new clients is insufficient.
- [ ] **F7 — `mp-bawb`:** require current-generation commit acceptance (`mp-rfnm`),
  private output staging (`mp-2wtc`), and retry budgets derived across resumes
  (`mp-c5wt`). Cover force/resume while an old attempt finishes, rejected or superseded
  publication, partial output failure, and repeated resumes against one budget.
  Verify historical readers (`mp-g315`) and explicitly bound detached execution
  (`mp-vu2v`). Stored fence metadata and reference-reducer tests alone do not prove
  enforcement.
- [ ] **F8 — `mp-skoo`:** use bounded, typed wait facts carrying gate, reason, elapsed
  time, identity, and release ownership.
  Catch `PoolSlotUnavailableError` before synthetic launch-failure records.
  Cover fail-fast, wait, and signal admission outcomes in scalar and fan-out paths.
  No prelaunch refusal may produce an execution attempt or consume its budget;
  production, replay, and resume must agree.
  Reuse the existing attempt boundary without introducing dormant retry-later policy.
- [ ] **F9 — `mp-q8xq`:** distinguish an explicitly closed empty expansion from an
  absent or unmaterialized roster.
  Decide whether completion needs an optional coverage/output requirement; preserve
  valid empty workflows and legacy runs.
  Test positive and negative CLI completion outcomes.
  Historical projection coverage tests do not establish this command-level contract.
- [ ] **F10 — `mp-rrkw`:** verify one run-owned abort decision prevents new launches,
  cancels and drains active siblings, and preserves the initiating cause and output
  evidence across nested scopes.
  Specify its interaction with tolerant fan-in and `continue_on_error` before closing
  `mp-cl0d` and the review finding.
- [ ] **Remaining contract extensions — `mp-d019`:** complete plugin classifier
  registration (`mp-3uaf`) and per-kind/label failure counts (`mp-m4vi`) against the
  [contract-failure design](../../design/contract-failure-primitives.md).
  Keep labels consumer-owned and preserve legacy records.
  These extensions do not block reporting causes the current system already knows.

## Other Execution Work

These existing beads retain their original scope, holds, and evidence requirements.
Moving them under the active epic does not approve a scheduler replacement or resume
paused work.

- `mp-rrfn` owns mapped-scope parity, recovery, resource adaptation, and scale evidence.
  `mp-3ci3` remains paused until those measurements justify changing reservation order
  or fairness behavior.
- `mp-82ls` remains paused pending a named recovery test that proves a missing durable
  guarantee. Its children keep their existing implementation dependencies; F7 supplies
  the required acceptance cases.
- `mp-tibt` and its remaining children (`mp-l3ot`, `mp-95xs`, `mp-txt9`, `mp-hoi1`,
  `mp-38j8`, `mp-0zqr`, `mp-uc23`) remain paused.
  Audit dormant retry-later mechanisms against observed need before retaining, removing,
  or integrating them.
  The distinct admission-record correction is owned by `mp-ux0f`.
- `mp-f77b`, `mp-9bx5`, `mp-0paw`, and `mp-7t7p` retain the existing triggers for
  persisted expansion, a ready-task scheduler, artifact lineage, and cross-scope force
  or budgets. Keep the current executor until those triggers are demonstrated.
- `mp-2ja3`, `mp-t4xc`, and `mp-1wf2` remain paused compatibility and decomposition
  work. Require stable smoke evidence before changing leaf APIs or extracting lifecycle
  abstractions.
- `mp-bq47` keeps successful-item targeted rerun behind a demonstrated operator need.
  Any selector must preserve parent, child, and artifact consistency without manual
  state edits. `mp-e3mg` owns portable process identity for full browser reconstruction
  after hydration; existing runtime table portability does not close it.
- `mp-vmjq` remains paused until measurements attribute launch or completion delays to
  contention in the default executor.
  Reuse existing execution ownership before adding another executor lifecycle.

Documentation is part of each implementation’s acceptance: update shipped help to the
behavior actually delivered, keep proposed behavior in this plan or referenced design
records, and test examples and diagnostic claims against the public CLI. Model-name
currency follows the separate maintenance cadence below.

Host-safety implementation remains under `mp-bd6v` and umbrella feature `mp-qigc`. Model
compatibility has its own [plan](plan-2026-09-10-model-catalog-followups.md) and epic
`mp-qmr0`. Neither workstream is implicitly implemented by this execution review.

## Testing and Rollout

Each correction needs a regression that demonstrates the original failure, the
applicable compatibility cases above, `make verify`, and CI on the published commit.
Host containment additionally needs the platform and rollout evidence required by its
plans. Paid model probes and downstream operational tests require their own execution
scope and budget.

Land bounded corrections independently.
Review artifact, API, and default changes with their migration plan.
The original RunPool review verification covers F1–F3 only; it does not count as
acceptance evidence for F4–F10.

## References

- [Review finding disposition](https://github.com/jlevy/metaproc/pull/75#issuecomment-5622046867)
- [Execution contracts](../../../../src/metaproc/docs/execution-model-design.md)
- [Execution implementation boundary](../../../../src/metaproc/docs/arch-execution-model.md)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
