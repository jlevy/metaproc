# Project Records

This directory keeps implementation plans, design records, and provenance separate from
the documentation that describes what Metaproc does.
That documentation ships inside the package; see
[README § Documentation](../../README.md#documentation), or run `metaproc help`.

The rule: a document here explains how the project got where it is, or where it might
go. A document that describes the system as it is belongs in `src/metaproc/docs/`, where
it ships.

## Current Work

The [roadmap](../../TODO.md) lists active releases and deferred quality work.
The tbd issue graph holds task-level status and dependencies.

## Specifications

- Active implementation plans use [`specs/active/`](specs/active/)
- [Completed plans](specs/done/) retain design decisions and rollout evidence

When implementation finishes, update the plan’s status and evidence before moving it
from `active` to `done`. Open follow-up work belongs in the roadmap or a new plan, not
in a completed specification.

## Research

[Research records](research/) preserve measurements, source analysis, and other evidence
used by plans without presenting proposed behavior as part of the shipped system.

The host-safety record set is divided by subject and update cadence:

- [Agent CLI Startup Memory](research/research-2026-09-01-agent-cli-memory-usage.md)
  owns comparative measurement, client demand, and profile identity.
- [Gemini CLI Project-State Startup Memory](research/research-2026-09-01-gemini-cli-project-state-memory.md)
  owns the controlled session-retention cause, source path, and state-isolation
  contract.
- [Host Memory Accounting and Control](research/research-2026-09-01-host-memory-accounting-and-control.md)
  owns platform gauges and the admission, pacing, and containment model.

Those records feed the
[RunPool Host Safety Envelope](specs/active/plan-2026-09-01-runpool-host-safety.md) and
the narrower
[Safeproc Local Incubation](specs/active/plan-2026-09-01-safeproc-local-incubation.md)
package plan. Consumer-specific incident records and deployment runbooks remain in the
consumer that owns them.

## Design Records

- [Design proposals](design/metaproc-design-proposals.md): the future-work backlog for
  the design doc. Not implemented; not shipped.
- [`design/backlog/`](design/backlog/): per-document future work, one file per shipped
  document that has any.
  Extracted so the shipped documents describe the system as it is.
- Revision histories: the authoring revisions of
  [metaproc-design.md](design/metaproc-design-revisions.md) and
  [arch-cloud-execution.md](design/arch-cloud-execution-revisions.md).
  These are authoring revisions, not releases; for what shipped when, see
  [CHANGELOG.md](../../CHANGELOG.md).

### Revising a shipped document

Use `tbd shortcut revise-architecture-doc`, which prompts you to verify content against
current code and then record future work.
Two rules the shortcut does not know about:

1. Future work goes in [`design/backlog/`](design/backlog/), not into the document.
2. A relative link in `src/metaproc/docs/` must resolve inside that directory, or it is
   dead for everyone reading the installed package.
   `devtools/check_shipped_links.py` enforces this.

Bump the **last updated** date in the document’s header when you make non-trivial
changes.

## Architecture Docs

The architecture documents ship in the package and are indexed with their
`metaproc help` topics in [README § Architecture](../../README.md#architecture).

## Releases

[`releases/`](releases/) holds per-release write-ups.
[CHANGELOG.md](../../CHANGELOG.md) is the canonical, complete history; the files here
are the longer-form notes for the releases that have one.

## Provenance

- [Standalone extraction](provenance/extraction.md) records how the public repository
  was derived and verified

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
