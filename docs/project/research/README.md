# Research Records

Research records preserve evidence that informs Metaproc design without describing
unimplemented behavior as current product documentation.
Active decisions and delivery work belong in [implementation plans](../specs/active/);
shipped behavior belongs in [`src/metaproc/docs/`](../../../src/metaproc/docs/).

## Process and Host Safety

- [Agent CLI Startup Memory](research-2026-09-01-agent-cli-memory-usage.md): measured
  startup curves, the Gemini CLI 0.40.1 session-retention cause, and matched one-shot
  controls for four agent CLIs
- [Host Memory Accounting and Control](research-2026-09-01-host-memory-accounting-and-control.md):
  macOS and Linux gauge semantics, process-tree attribution, and the separation between
  admission, launch pacing, and emergency containment

These documents contain project-neutral findings extracted from downstream integration
work. Consumer-specific run identifiers, private issue references, local paths, and raw
operational artifacts remain with the consumer that owns them.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
