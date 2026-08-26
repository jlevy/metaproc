---
title: Documentation Organization
description: Put every project-internal document under docs/project, restore the core design doc to its real role and name, and reconcile the two concepts docs so one vocabulary spans the general model and the shipped manuals.
date: 2026-08-26
status: Draft
---
# Feature: Documentation Organization

**Date:** 2026-08-26

**Status:** Draft

## Overview

Metaproc’s documentation is well maintained and badly filed.
Links are checked in CI and none are broken, the architecture index matches disk
exactly, and the shipped-manual wiring is deliberately drift-proof.
What is wrong is the shape: `docs/` mixes reader-facing entry points with project
internals, the largest and most authoritative document in the repository is filed as one
architecture doc among eight, and the two concepts documents describe the same system in
two vocabularies that contradict each other on at least five terms.

This plan moves every project-internal document under `docs/project/`, keeps the top
level to the few documents a newcomer actually needs, restores the core design document
to its real role and name, and reconciles the general and shipped concepts docs against
one vocabulary.

## Goals

- `docs/` top level holds only reader-facing entry points; everything project-internal
  lives under `docs/project/`.
- The core design document is named and framed as a design document, not as one
  architecture reference among peers.
- The top-level README links project documents directly, says which are also readable
  through `metaproc help`, and states the order in which the three core documents should
  be read.
- The general and shipped concepts docs share one vocabulary, with every remaining
  divergence deliberate and stated.
- No broken links at any point; `devtools/check_links.py` stays green.

## Non-Goals

- Rewriting document *content*, beyond the terminology reconciliation in Phase 3 and the
  reframing front matter in Phase 2.
- Changing what `metaproc help` topics exist, or adding any document to the wheel.
  The shipped manual set stays exactly `operator`, `developer`, `concepts`.
- Reorganizing `docs/runbooks/`, which is already coherent and reader-facing.
- Fixing the arch-doc `last updated` drift.
  That is a separate enforcement concern, tracked as its own bead.

## Background

A documentation audit on 2026-08-26 inventoried 45 first-party documents and found the
structure sound but the placement inconsistent.

Four findings drive this plan.

**The `docs/project` rule exists and is not applied.** `docs/project/README.md` states
it “keeps implementation plans and provenance separate from user and operator
documentation.” That rule is right, but architecture, design, release notes, and
performance references all sit outside it.
`docs/project/README.md` also documents a `specs/future/` directory that has never
existed on disk.

**`arch-metaproc-core.md` is the original design doc wearing an architecture doc’s
filename.** The evidence is in the file.
It carries 19,926 words against 8,792 for the next largest architecture doc.
It has 56 numbered sections where no other document has more than 4. Its numbering
starts at §5, and it says why: “numbering starts at 5 because earlier sections moved
into the concepts doc and the companion arch docs.”
It carries a Revision History running rev2i through rev2n — the same rev2i that
`metaproc-design-rev3-proposals.md` names as its base.
The seven other `arch-*.md` files are focused component references, which is what an
architecture doc should be.

**The two concepts docs disagree on vocabulary.** `docs/process-framework-concepts.md`
(general model) and `src/metaproc/docs/metaproc-concepts-and-principles.md` (shipped in
the wheel) define overlapping terms differently, and in one case flatly contradict each
other. Term counts across the pair:

| Term | General | Shipped | Status |
| --- | --- | --- | --- |
| roster | 24 | 1 | The 1 is a disclaimer saying the framework does not use it |
| task | 64 | 8 | General calls it “the pivotal object”; shipped calls it “not an authored process object” |
| attempt | 32 | 5 | Core object in general; incidental in shipped |
| commit | 17 | 1 | The 1 is `git commit`, unrelated |
| expansion | 14 | 1 | The 1 is “variable expansion”, unrelated |
| closure | 7 | 0 | Absent from shipped |
| fencing | 3 | 0 | Absent from shipped |
| generation | 10 | 0 | Absent from shipped |

**The shipped manuals are linked as commands, not as documents.** README rows 89-91 make
the invocation the row and the document a parenthetical `(source)`. Those three files
hold 13,742 words, including the second-largest document in the repository.
A GitHub reader cannot run `metaproc help`.

## Design

### Target layout

```
docs/
  installation.md              user entry
  development.md               contributor entry
  runbooks/                    operator procedures (7)
  project/
    README.md                  index for everything below
    design/
      metaproc-design.md               <- docs/arch/arch-metaproc-core.md
      metaproc-design-proposals.md     <- docs/metaproc-design-rev3-proposals.md
      execution-model-design.md        <- docs/execution-model-design.md
      process-framework-concepts.md    <- docs/process-framework-concepts.md
    arch/                      7 focused component references
    specs/{active,done}/
    provenance/
    releases/                  <- docs/releases/
    conventions.md             <- docs/conventions.md
    artifact-catalog.md        <- docs/artifact-catalog.md
    performance-notes.md       <- docs/performance-notes.md
    memory-accounting-reference.md
    agent-toolchain-bootstrap.md
    publishing.md
```

### Why the core doc becomes the design doc

`arch-metaproc-core.md` is renamed to `docs/project/design/metaproc-design.md`. This is
a restoration, not an invention: the file already carries the design doc’s revision
lineage and says in its own scope section that its first four sections were moved
elsewhere. Naming it `metaproc-design.md` also matches what contributors already call it
from memory.

The remaining seven `arch-*.md` files stay architecture docs.
They are what the category is for: focused component references, each 1,179 to 8,792
words, covering RunPool admission and memory pressure, the Claude Code harness, cloud
execution, authentication, file IO, the execution model, and testing.

### What ships in the wheel: nothing new

The shipped manual set stays `operator`, `developer`, `concepts` and nothing is added.

For the design doc, the reasons not to bake it in are its size and its kind.
At 19,926 words it would more than double the wheel’s 13,742-word documentation payload,
and a document with a Revision History and a Future Considerations section is a design
record, not a help topic.
`metaproc help` topics are task-oriented: how to run something, how to extend something,
how to think about the model.

For the focused architecture docs, the same holds with less argument.
How RunPool senses memory pressure is a contributor concern, not an operator one.

`docs/project/design/process-framework-concepts.md` also stays out of the wheel.
It is the general model, not an operator reference.
Phase 3 aligns it with the shipped concepts doc rather than shipping it.

The link direction is the fix instead: README links these directly, and
`metaproc help developer` routes to the design doc.

### The reading path

There is an order in which these documents actually make sense, and no current index
states it.
Someone arriving at the repository wanting to understand Metaproc should read:

1. **`metaproc-concepts-and-principles.md`** — the vocabulary, the ownership boundaries,
   the four step modes, the optimization loops.
   Everything else assumes it; the design doc says so in its own scope section: “read it
   first for the definitions assumed below.”
2. **`metaproc-design.md`** — how the system is actually built, in detail.
   This is the document whose §5 numbering exists *because* the first four sections
   became doc 1.
3. **`process-framework-concepts.md`** — the general model beneath any process
   framework, plus the explicit map of how Metaproc instantiates it and where it
   deviates. A reference for readers who want the theory, not a prerequisite.

Docs 2 and 3 become siblings in `docs/project/design/` under this plan, which is part of
why `design/` is the right name for that directory.
Doc 1 stays in `src/metaproc/docs/` because it ships in the wheel — the one member of
the path that lives elsewhere, for a reason worth stating rather than hiding.

This ordering is the organizing principle for the README rewrite below, and it is why
the design doc must be linked prominently even though it moves under `docs/project/`.
Filing something as a project record must not mean burying it: this is the second
document a new contributor should read.

### README changes

The Start Here table gets the document as the row and the command as an annotation,
inverting the current presentation:

| Document | Also via | Purpose |
| --- | --- | --- |
| `metaproc-concepts-and-principles` (linked to its path) | `metaproc help concepts` | … |

Start Here then presents the three documents above as a numbered path, in order, with
one line each on what it answers and whether it is required or optional.
A new Project Documentation section links the design doc, the proposals doc, the
architecture index, and the project records index directly.

### Terminology reconciliation

`src/metaproc/docs/metaproc-concepts-and-principles.md` is the older document and holds
the shipped vocabulary; `process-framework-concepts.md` is the general model written
later. Neither is automatically authoritative: the general doc reflects later thinking,
the shipped doc reflects what the code actually does today.

The rule this plan adopts: **where the two disagree about what Metaproc does, the
shipped doc wins and the general doc adopts its term.
Where they disagree about the model, the general doc keeps its term and the shipped doc
gains an explicit pointer saying the concept is modeled but not implemented.**

Each divergence becomes a sub-bead.
The five known ones are enumerated in Phase 3.

## Implementation Plan

### Phase 1: Move and rename

Mechanical. Every step is `git mv` plus a link sweep, verified by
`python -m devtools.check_links`.

- [ ] Create `docs/project/design/` and `docs/project/arch/`.
- [ ] `git mv docs/arch/arch-metaproc-core.md docs/project/design/metaproc-design.md`
  (101 references across 53 files; the largest sweep in this plan).
- [ ]
  `git mv docs/metaproc-design-rev3-proposals.md docs/project/design/metaproc-design-proposals.md`
  and drop “rev3” from its title and prose, since a specifically-numbered next revision
  is not committed to.
- [ ] Move `execution-model-design.md` and `process-framework-concepts.md` into
  `docs/project/design/`.
- [ ] Move the remaining seven `docs/arch/arch-*.md` into `docs/project/arch/`.
- [ ] Move `releases/`, `conventions.md`, `artifact-catalog.md`, `performance-notes.md`,
  `memory-accounting-reference.md`, `agent-toolchain-bootstrap.md`, and `publishing.md`
  under `docs/project/`.
- [ ] Update every inbound link, including the Python docstrings in
  `src/metaproc/execution_model/` and the path constant in
  `tests/test_locking_policy.py`.
- [ ] Remove the `specs/future/` sentence from `docs/project/README.md` and index the
  new `design/`, `arch/`, and `releases/` subdirectories there.
- [ ] Move the arch index out of `docs/development.md` into `docs/project/README.md`,
  leaving `development.md` a pointer.
- [ ] `make verify`.

### Phase 2: Reframe and relink

- [ ] Rewrite `metaproc-design.md` front matter: title “Metaproc Design”, description
  and status reflecting a design record.
  Keep the section numbering, the Revision History, and the §5 explanation, which are
  the reasons for the rename.
- [ ] Rewrite the README Documentation section: present the three-document reading path
  in order, invert Start Here so documents are rows and `metaproc help` is an
  annotation, add Project Documentation, link `docs/project/releases/` and state its
  relationship to `CHANGELOG.md`.
- [ ] Route `metaproc help developer` to the design doc by path.
- [ ] Link `src/metaproc/runpool/README.md` from `arch-runpool.md`, or fold it in and
  delete it.
- [ ] Refresh `TODO.md` § Current Release from v0.2.1 to v0.3.0.
- [ ] `make verify`.

### Phase 3: Reconcile the concepts docs

One sub-bead per divergence.

- [ ] **roster.** The shipped doc says “Analysis-domain code uses *roster* as a synonym;
  the framework does not.”
  The general doc defines roster as a core term and uses it 25 times, and
  `metaproc-design.md` uses it 32 times including as a value in its own `role` enum and
  in its reference examples.
  Decide one way and make all three agree.
- [ ] **task.** General: “the pivotal object in this model … the correct unit of
  scheduling, of failure, and of resume.”
  Shipped: “a runtime term used by state and log paths; it is not an authored process
  object.” Both are defensible about different layers; neither says so.
  State the layer each is talking about.
- [ ] **variant.** General makes it part of task identity.
  Shipped §4.1 makes it a run-level adapter selector.
  Reconcile or scope explicitly.
- [ ] **expansion, closure, generation.** Core objects in the general model, absent from
  the shipped glossary.
  Add pointers marking them modeled-but-not-implemented.
- [ ] **commit, fencing.** Same treatment.
  The general doc’s own deviations list already says Metaproc has “no single commit
  record covering a multi-output task”; the shipped glossary does not mention the
  concept.
- [ ] Add a short cross-reference header to each doc naming the other and saying which
  owns what.
- [ ] Re-read both docs end to end for divergences beyond these five and file any found.
- [ ] `make verify`.

## Testing Strategy

- `python -m devtools.check_links` after every move; it already gates `make verify` and
  is the safety net for a 97-reference rename.
- `make verify` at the end of each phase: public hygiene, distribution inspection, and
  the Agent Skill drift test all touch documentation paths.
- `uv run pytest tests/test_locking_policy.py` specifically, since it hardcodes
  `src/metaproc/runpool/README.md`.
- Confirm the wheel’s documentation payload is unchanged: nothing is added to or removed
  from `src/metaproc/docs/`, so `devtools/check_distribution.py` should report no delta.
- Manual: browse README on the branch and confirm a reader reaches the design doc and
  the shipped manuals without running the CLI.

## Rollout Plan

One pull request per phase, in order, each with green CI. Phase 1 is a large diff that
is almost entirely renames; review it with `git log --follow` and `git diff -M` so the
moves read as moves.
Phases 1 and 2 can land close together.
Phase 3 changes prose that people rely on and deserves its own review pass.

No user-facing behavior changes and no wheel contents change, so no release note is
required beyond a CHANGELOG entry noting the documentation paths moved.

## Open Questions

- Do `conventions.md` and `artifact-catalog.md` belong under `docs/project/` or at the
  top level? Both are referenced by operators inspecting artifacts, not only by
  contributors. This plan moves them; leaving them at top level is equally defensible.
- Should `docs/project/releases/` merge into `CHANGELOG.md` entirely?
  The two overlap, and the per-release files have almost no inbound links.
- After Phase 3, is `process-framework-concepts.md` still the right name, given it will
  sit under `design/` next to `metaproc-design.md`?
- Is `docs/project/` the right name for a directory whose `design/` subdirectory holds
  the second document a new contributor should read?
  The rule it encodes — internals separate from user and operator docs — is sound, but
  “project records” undersells what now lives there.
  `docs/internals/` would describe the contents more honestly.
  Deferred because renaming twice is worse than renaming once late.

## References

- Documentation audit, 2026-08-26. Figures re-measured against `d17b493`, the base this
  plan merges into; the audit’s conclusions were unchanged by that rebase.
- `docs/project/README.md` — the project-records rule this plan applies
- `devtools/check_links.py` — the gate that makes a rename of this size safe
