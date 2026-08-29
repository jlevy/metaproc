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

### Potential Improvements

The two implementation increments in the adoption path remain the relevant improvements:
persist attempts and task generations as durable facts, then replace the level walk and
its aligned-chain bridge with an incremental task-level scheduler.
The trigger table above defines when the second increment is warranted.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
