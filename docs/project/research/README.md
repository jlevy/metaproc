# Research Records

Research records preserve evidence that informs Metaproc design without describing
unimplemented behavior as current product documentation.
Active decisions and delivery work belong in [implementation plans](../specs/active/);
shipped behavior belongs in [`src/metaproc/docs/`](../../../src/metaproc/docs/).

## Process and Host Safety

- [Agent CLI Startup Memory](research-2026-09-01-agent-cli-memory-usage.md): measured
  startup curves, matched one-shot controls for four agent CLIs, profile identity, and
  the remaining benchmark matrix
- [Gemini CLI Project-State Startup Memory](research-2026-09-01-gemini-cli-project-state-memory.md):
  the controlled project-history cause, durable-state semantics, upstream source path,
  and the state boundary for fresh and resumed orchestrated runs
- [Host Memory Accounting and Control](research-2026-09-01-host-memory-accounting-and-control.md):
  macOS and Linux gauge semantics, process-tree attribution, and the separation between
  admission, launch pacing, and emergency containment

Together, these records are the authoritative project-neutral account of the downstream
agent-resource research available on 2026-09-01. They preserve the reusable
measurements, causal analysis, source review, state-isolation contract, operating-system
accounting, guard evidence, workflow controls, and open evidence gaps.
Consumer-specific run identifiers, private issue references, local paths, deployment
tasks, and raw operational artifacts remain with the consumer that owns them.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
