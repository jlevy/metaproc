# Architecture: Execution Model: Future Work

Backlog extracted from
[arch-execution-model.md](../../../../src/metaproc/docs/arch-execution-model.md), which
ships in the wheel and describes the system as it is.
Where it might go is a project record and lives here.

## Future Considerations

### Open Questions

The reducer does not model admission and budget reservation, finalization and effects,
`group_by`, threshold cardinality, or the legacy barrier semantics of `needs`. The
design specifies admission claims and authorities, while RunPool remains the local
implementation.

Retry policy is data on `StepTemplate` rather than a scheduler constant, so the model
can replay a spec whose policy differs from the defaults.
The semantics version belongs to the resolved plan and must be enforced by the compiler;
storing it in scheduler state would not enforce anything.

A composite step is the one executable kind with no durable task record of its own.
Code, agent, manual, and mapped-composite items all mark a running attempt and a
terminal one; a scalar composite marks neither, because there is no settled answer to
what an attempt is for a step whose work is a whole child DAG. Is an attempt one
evaluation of the child scope, or does the child’s own per-step attempt history already
carry the fact? And where would the record live, given that the child scope already owns
`<run>/<step>/`?

The question is not academic.
`_is_step_completed` reads the per-task record first and falls back to
`process-status.yaml`, and the orchestrator rewrites every active step in that
projection to `pending` before the level loop reaches the completion check.
That reset is deliberate: without it a monitor racing a resume observes a stale terminal
state while new work is already running.
The fallback is equally deliberate, because the projection is authoritative for fan-out
steps, which have no single per-task record.
A scalar composite has neither a per-task record nor that exemption, so it is re-entered
on every bare same-`RUN_ID` resume: the child spec is reparsed and replanned, its
projection reset, its outputs revalidated, and a fresh `process_start` and set of
`step_skip` records appended to its event stream.
Child leaves still skip, because they own durable records, so no handler or agent runs.
The cost is wasted structural work and an event log that accretes a new start per
resume.

Answering the attempt question settles the defect as a side effect: a composite that
marks its own completion never reaches the fallback, and composites gain the attempt
history and result records they currently lack.
Patching the fallback instead is the tempting shortcut and the worse trade.
It is load-bearing for fan-out steps, the snapshot it would have to read is the stale
terminal state the reset exists to suppress, and the failure modes are not symmetric:
the present defect repeats work, while a wrong reuse predicate skips it.

This is a concrete instance of the first adoption-path increment below.
Tracked as `mp-5nko` for the question and `mp-ad60` for the defect.

### Potential Improvements

The two implementation increments in the adoption path remain the relevant improvements:
persist attempts and task generations as durable facts, then replace the level walk and
its aligned-chain bridge with an incremental task-level scheduler.
The trigger table above defines when the second increment is warranted.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
