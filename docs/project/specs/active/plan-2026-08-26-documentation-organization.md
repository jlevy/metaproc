---
title: Documentation Organization
description: Ship every core Metaproc document inside the wheel behind `metaproc help`, index all of them from the README with their CLI equivalents, and move project-internal material — backlog, revision history, maintenance scaffolding — out of the shipped set and into docs/project.
date: 2026-08-26
status: Implemented
---
# Feature: Documentation Organization

**Date:** 2026-08-26

**Status:** Implemented

## Overview

Metaproc’s documentation is well maintained and badly filed.
Links are checked in CI and none are broken, the architecture index matches disk
exactly, and the shipped-manual wiring is deliberately drift-proof.
What is wrong is reach and shape: an agent working inside a downstream package that
depends on Metaproc can read three manuals through `metaproc help` and nothing else,
while the document that actually explains how the system is built sits in `docs/arch/`
under a filename that hides it; six of the eight architecture docs are not linked from
the README at all; and the two concepts documents describe the same system in two
vocabularies that contradict each other on at least five terms.

This plan makes the core documentation set part of the package.
Every document core to the system ships in the wheel, is served by
`metaproc help <topic>`, and is listed and linked in the README alongside its CLI
equivalent. Everything project-internal — implementation plans, provenance, release
records, revision histories, and future-work backlogs — moves under `docs/project/` and
stays out of the wheel.

The reorganization is deliberately phased.
Phase 1 moves documents without editing prose.
Only once the whole set sits in one directory is it reviewed as a set, and only then is
it tightened and stripped of internal material.
Moving and rewriting at the same time would make a large diff impossible to review.

## Goals

- Every document core to the system ships in `src/metaproc/docs/` and is reachable as a
  `metaproc help <topic>` topic, so an agent using Metaproc from an installed wheel has
  the complete picture without the repository.
- The README lists and links every first-party document, and names the `metaproc help`
  equivalent for each one that has it.
- Shipped documents contain no project-internal material: no future-work backlog, no
  internal revision history, no repository-maintenance instructions.
  Where a shipped document refers to a version, it refers to an actual release.
- Cross-document links inside the shipped set resolve for a reader of the installed
  wheel, not only for a reader of the repository.
- `docs/` keeps only what a repository reader needs; everything project-internal lives
  under `docs/project/`.
- The general and shipped concepts docs share one vocabulary, with every remaining
  divergence deliberate and stated.
- No broken links at any point; `devtools/check_links.py` stays green.

## Non-Goals

- Rewriting document *content* during Phase 1. Prose changes are Phases 4 through 6,
  after the set can be read as a set.
- Reorganizing `docs/runbooks/`, which is already coherent and reader-facing.
  Runbooks are operator procedures for this repository, not framework documentation, and
  they stay out of the wheel.
- Fixing the arch-doc `last updated` drift generally.
  That is a separate enforcement concern, tracked as its own bead.
- Changing any runtime behavior, artifact shape, or CLI flag.
  The only CLI change is the set of `metaproc help` topics and the topic listing format.

## Background

A documentation audit on 2026-08-26 inventoried 45 first-party documents and found the
structure sound but the placement inconsistent.
Five findings drive this plan.

**Core documentation does not reach the people who need it.** `metaproc help` serves
three topics totaling 13,742 words.
Everything else — the design document, all eight architecture documents, the
conventions, the artifact catalog — exists only in the repository.
An agent operating Metaproc as a dependency cannot read any of it.
That is the gap this plan is mainly about, and it inverts the earlier draft of this
plan, which proposed adding nothing to the wheel.

**The `docs/project` rule exists and is not applied.** `docs/project/README.md` states
it “keeps implementation plans and provenance separate from user and operator
documentation.” That rule is right, but architecture, design, release notes, and
performance references all sit outside it.
`docs/project/README.md` also documents a `specs/future/` directory that has never
existed on disk.

**`arch-metaproc-core.md` is the design doc wearing an architecture doc’s filename.**
The evidence is in the file.
It carries 19,926 words against 8,792 for the next largest architecture doc.
It has 56 numbered sections where no other document has more than 4. Its numbering
starts at §5, and it says why: “numbering starts at 5 because earlier sections moved
into the concepts doc and the companion arch docs.”
It carries a Revision History running rev2e through rev2o. The seven other `arch-*.md`
files are focused component references, which is what an architecture doc should be.

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

**The README indexes some documents and not others.** Six of the eight `arch-*.md` files
are not linked from the README at all, nor is `docs/releases/`, nor
`src/metaproc/runpool/README.md`. Where the shipped manuals are listed, README rows
89-91 make the invocation the row and the document a parenthetical `(source)`, so a
GitHub reader sees a command they cannot run instead of a document they can read.

## Design

### What ships: the core set

Fifteen topics, 75,844 words.
Twelve are new; the three existing topics are unchanged in content by this plan.

| Topic | File in `src/metaproc/docs/` | Words | Status |
| --- | --- | --- | --- |
| `concepts` | `metaproc-concepts-and-principles.md` | 6,996 | ships today |
| `operator` | `metaproc-operator-reference.md` | 5,402 | ships today |
| `developer` | `metaproc-developer-guide.md` | 1,344 | ships today |
| `design` | `metaproc-design.md` | 19,926 | from `docs/arch/arch-metaproc-core.md` |
| `framework` | `process-framework-concepts.md` | 7,214 | from `docs/` |
| `conventions` | `conventions.md` | 4,515 | from `docs/` |
| `execution-model` | `execution-model-design.md` | 1,877 | from `docs/` |
| `artifacts` | `artifact-catalog.md` | 1,363 | from `docs/` |
| `arch-auth` | `arch-authentication.md` | 8,792 | from `docs/arch/` |
| `arch-cloud` | `arch-cloud-execution.md` | 6,178 | from `docs/arch/` |
| `arch-runpool` | `arch-runpool.md` | 4,041 | from `docs/arch/` |
| `arch-harness` | `arch-claude-code-harness.md` | 2,946 | from `docs/arch/` |
| `arch-execution` | `arch-execution-model.md` | 2,686 | from `docs/arch/` |
| `arch-testing` | `arch-testing.md` | 1,385 | from `docs/arch/` |
| `arch-file-io` | `arch-file-io-utilities.md` | 1,179 | from `docs/arch/` |

`docs/arch/` is emptied and removed by this move.
The seven component references stay recognizably a family through their `arch-` filename
and topic prefix, which is what the directory was carrying.

### Why the whole set, and not just the design doc

Shipping the design document alone would leave it linking to documents that are not
there. It carries 30 repository-relative links, 16 of them to `arch-*.md` siblings.
`devtools/check_links.py` validates local links against the repository, so those links
stay green in CI while being dead for every reader of the installed wheel — the failure
mode is invisible to the gate that exists.

Moving the whole set into one directory converts those 16 links into sibling links that
resolve in the repository *and* in the wheel, with no rewrite and no new class of
unchecked absolute URL. That property is the reason to take the arch docs along, and it
is worth protecting with a gate (below) rather than leaving to discipline.

The same argument settles what stays behind.
`development.md`, `publishing.md`, `agent-toolchain-bootstrap.md`, and the runbooks are
about working on this repository, not about the framework.
They stay in `docs/`, and shipped documents must not link to them.

### The shipped-link rule and its gate

**Every relative link in a shipped document must resolve to another shipped document.**
Anything else is either an absolute `https://github.com/jlevy/metaproc/...` URL or is
rewritten away.

`check_links.py` cannot catch a violation today: a link from `src/metaproc/docs/` to
`../../../docs/development.md` resolves inside the repository and passes.
This plan adds a check that walks `src/metaproc/docs/*.md` and fails any relative link
whose target escapes that directory.
Without it the wheel accumulates dead links again, silently, exactly as it has.

Note that this rule is already violated today, before any move: the three shipped
manuals carry 33 relative links between them, 26 of which point outside
`src/metaproc/docs/`. Most of those targets join the shipped set under this plan; the
remainder are Phase 1 fixes.

### What is internal and does not ship

A shipped document describes the system as it is.
Project-internal material describes how the project got there or where it might go, and
belongs under `docs/project/`. Three categories come out of the documents that move:

- **Future-work backlog.** The `Future Considerations` section of the design doc (Open
  Questions and Potential Improvements, including its `[unverified]` audit markers), its
  §16 `Optional Workspace/State Surface (Future)`, and the equivalent sections in each
  arch doc. `metaproc-design-rev3-proposals.md` is entirely backlog and stays in
  `docs/project/design/`; shipped documents must not link to it.
- **Internal revision history.** The design doc’s `Revision History` (rev2e through
  rev2o) and its `Revision: rev2m` header line.
  These are authoring revisions, not releases, and mean nothing to a reader of the
  package. They move to `docs/project/design/metaproc-design-revisions.md`.
- **Repository-maintenance scaffolding.** All eight arch docs carry a Maintenance
  blockquote instructing the reader to run `tbd shortcut revise-architecture-doc` and
  pointing at `development.md § Architecture docs`. That is an instruction to a
  contributor to this repository, addressed to a reader who may have neither.

**Where a shipped document refers to a version, it refers to a release.** Replacing the
revision markers is not merely deletion: a statement dated by `rev2n` should be dated by
the release it shipped in.
The mapping is unambiguous from the tags:

| Revisions | Release |
| --- | --- |
| rev2i and earlier (≤ 2026-04-20) | before v0.2.0 |
| rev2j, rev2k (2026-08-02/03) | v0.2.1 (2026-08-09) |
| rev2l (2026-08-09) | v0.2.1 |
| rev2m, rev2n (2026-08-24) | v0.3.0 (2026-08-24) |
| rev2o (2026-08-25) | unreleased at time of writing |

### `metaproc help` at fifteen topics

Two changes follow from the size of the set.

**The topic listing shows sizes.** `metaproc help` with no topic currently prints name
and description. At fifteen topics, one of them 19,926 words, an agent choosing a topic
is choosing how much of its context to spend — roughly 30,000 tokens for `design` alone.
The listing gains an approximate word count per topic so that choice is informed rather
than discovered afterward.

**The topic registry replaces the dataclass fields.** `HelpTopics` is a frozen dataclass
with one field per topic and `skill/builtin.py` enumerates topics via
`dataclasses.fields`. Fifteen fields is unwieldy, and field names are Python
identifiers, so a dashed topic name like `arch-file-io` cannot be a field name at all.
Phase 1 replaces it with an explicit registry mapping topic name to filename,
description, and approximate size, with `TOPIC_DESCRIPTIONS` derived from the registry.

`HelpTopics` and `TOPIC_DESCRIPTIONS` are imported by `commands/help.py` and
`skill/builtin.py` only, both first-party, so this is an internal refactor.
Per AGENTS.md the shape is nonetheless treated as public: the registry keeps
`TOPIC_DESCRIPTIONS` exported with its current type, and `load_help_topics()` keeps
working for the three existing topics.

### The reading path

There is an order in which these documents make sense, and no current index states it.
Someone arriving at Metaproc should read:

1. **`concepts`** — the vocabulary, the ownership boundaries, the four step modes, the
   optimization loops. Everything else assumes it; the design doc says so in its own
   scope section: “read it first for the definitions assumed below.”
2. **`design`** — how the system is actually built, in detail.
   This is the document whose §5 numbering exists *because* the first four sections
   became doc 1.
3. **`framework`** — the general model beneath any process framework, plus the explicit
   map of how Metaproc instantiates it and where it deviates.
   A reference for readers who want the theory, not a prerequisite.

Under this plan all three ship and all three are `metaproc help` topics, so the path is
identical from the repository and from an installed wheel.
This ordering is the organizing principle for the README rewrite.

### README changes

Two rules, applied to every table in the Documentation section.

**The document is the row; the command is a column.** The Start Here table inverts:

| Document | `metaproc help` | Purpose |
| --- | --- | --- |
| `metaproc-concepts-and-principles` — linked to its path | `concepts` | … |

**Every first-party document appears somewhere.** The audit found six arch docs,
`docs/releases/`, and `src/metaproc/runpool/README.md` linked from nowhere.
The Documentation section grows an Architecture table listing all seven arch docs with
their topics, and a Project Records section covering `docs/project/` and
`docs/releases/`.

### Target layout

```
src/metaproc/docs/           the shipped set — 15 topics
  metaproc-concepts-and-principles.md
  metaproc-operator-reference.md
  metaproc-developer-guide.md
  metaproc-design.md                  <- docs/arch/arch-metaproc-core.md
  process-framework-concepts.md       <- docs/
  conventions.md                      <- docs/
  execution-model-design.md           <- docs/
  artifact-catalog.md                 <- docs/
  arch-*.md                           <- docs/arch/ (7 files)

docs/
  installation.md            user entry
  development.md             contributor entry
  runbooks/                  operator procedures for this repo (7)
  project/
    README.md                index for everything below
    design/
      metaproc-design-proposals.md    <- docs/metaproc-design-rev3-proposals.md
      metaproc-design-revisions.md    <- extracted Revision History
      backlog/                        <- extracted Future Considerations, per doc
    specs/{active,done}/
    provenance/
    releases/                         <- docs/releases/
    performance-notes.md              <- docs/
    memory-accounting-reference.md    <- docs/
    agent-toolchain-bootstrap.md      <- docs/
    publishing.md                     <- docs/
```

## Implementation Plan

### Phase 1: Move and wire

Mechanical. `git mv` plus a link sweep plus the topic registry.
No prose is edited beyond what a link rewrite requires.

- [ ] `git mv docs/arch/arch-metaproc-core.md src/metaproc/docs/metaproc-design.md` (105
  references across 52 files; the largest sweep in this plan).
- [ ] `git mv` the seven remaining `docs/arch/arch-*.md` into `src/metaproc/docs/`;
  remove the empty `docs/arch/`.
- [ ] `git mv` `conventions.md`, `artifact-catalog.md`, `process-framework-concepts.md`,
  and `execution-model-design.md` from `docs/` into `src/metaproc/docs/`.
- [ ] Replace `HelpTopics`/`TOPIC_DESCRIPTIONS` in `src/metaproc/docs/__init__.py` with
  a topic registry carrying topic name, filename, description, and approximate word
  count; keep `TOPIC_DESCRIPTIONS` exported and `load_help_topics()` working.
- [ ] Update `src/metaproc/skill/builtin.py` to enumerate topics from the registry
  instead of `dataclasses.fields(HelpTopics)`.
- [ ] Update `src/metaproc/commands/help.py` to print approximate sizes in the topic
  listing.
- [ ] Add the twelve new filenames to the required-suffix sets in
  `devtools/check_distribution.py` (both `_inspect_wheel` and `_inspect_sdist`).
- [ ] Add `devtools/check_shipped_links.py`: every relative link in
  `src/metaproc/docs/*.md` must resolve within that directory.
  Wire it into `make lint-check`.
- [ ] Fix the pre-existing violations that rule exposes in the three current manuals (26
  links pointing outside `src/metaproc/docs/`), and any created by the move.
- [ ] Update every inbound link repository-wide, including the Python docstrings in
  `src/metaproc/execution_model/` and the path constant in
  `tests/test_locking_policy.py`.
- [ ] Regenerate the Agent Skill: `metaproc skill metaproc --install`.
- [ ] `make verify`.

### Phase 2: README and link direction

- [ ] Rewrite the README Documentation section: document as row, `metaproc help` topic
  as a column, the three-document reading path stated in order, an Architecture table
  covering all seven arch docs, and a Project Records section.
- [ ] Link `docs/releases/` and state its relationship to `CHANGELOG.md`.
- [ ] Link `src/metaproc/runpool/README.md` from `arch-runpool.md`, or fold it in and
  delete it.
- [ ] Route `metaproc help developer` to the design doc by topic name.
- [ ] Remove the `specs/future/` sentence from `docs/project/README.md`; index
  `design/`, `releases/`, and the moved reference docs there.
- [ ] Move the arch index out of `docs/development.md` into `docs/project/README.md`,
  leaving `development.md` a pointer.
- [ ] Refresh `TODO.md` § Current Release from v0.2.1 to v0.3.0.
- [ ] `make verify`.

### Phase 3: Cohesion review

The set has never been read as a set.
This phase produces findings and beads, not edits.

- [ ] Read all fifteen shipped documents end to end and record overlap, contradiction,
  and gaps.
- [ ] Resolve the naming collision the move creates: `execution-model`
  (`execution-model-design.md`) and `arch-execution` (`arch-execution-model.md`) are two
  topics about the execution model.
  Decide whether they merge, or what each is named to make the split obvious.
- [ ] Decide whether the `arch-` prefix still earns its place once the directory that
  gave it meaning is gone.
- [ ] Assess §7 of the design doc, the illustrative downstream analysis profile, against
  the consumer-agnostic rule in AGENTS.md.
  Its own Future Considerations proposes moving it out.
- [ ] Confirm the reading path holds for a wheel reader with no repository.

### Phase 4: Externalize internal material

- [ ] Extract the design doc’s `Revision History` to
  `docs/project/design/metaproc-design-revisions.md` and remove the `Revision: rev2m`
  header line.
- [ ] Extract `Future Considerations` from the design doc and from each arch doc to
  `docs/project/design/backlog/`, one file per source document, each linked from
  `docs/project/README.md`.
- [ ] Remove §16 `Optional Workspace/State Surface (Future)` from the design doc to the
  same backlog.
- [ ] Remove the Maintenance blockquote from all eight moved documents; state the
  revision convention once in `docs/project/README.md` instead.
- [ ] Replace revision references in shipped prose with release versions per the mapping
  in Design.
- [ ] Fix the header drift the extraction exposes: the design doc’s header claims
  `Revision: rev2m` and `last updated 2026-08-24` while its newest history entry is
  rev2o, dated 2026-08-25.
- [ ] Correct the design doc’s companion list, which links to itself and omits
  `arch-execution-model` and `arch-file-io-utilities`.
- [ ] Drop the two links to `metaproc-design-rev3-proposals.md` from the shipped design
  doc.
- [ ]
  `git mv docs/metaproc-design-rev3-proposals.md docs/project/design/metaproc-design-proposals.md`
  and drop “rev3” from its title and prose, since a specifically-numbered next revision
  is not committed to.
- [ ] `git mv` `releases/`, `performance-notes.md`, `memory-accounting-reference.md`,
  `agent-toolchain-bootstrap.md`, and `publishing.md` under `docs/project/`.
- [ ] `make verify`.

### Phase 5: Tighten

75,844 words is a large payload to hand an agent, and the Phase 3 findings are the input
here. The target is not a word count; it is that each document has one job.

- [ ] Cut the duplication between the design doc §21 and `arch-cloud-execution.md`,
  which the design doc’s own backlog names as a maintenance burden.
- [ ] Act on the Phase 3 overlap findings.
- [ ] Add a reading guide to the design doc: 56 sections with no map is the single
  largest usability problem in the set, and its own backlog proposes the fix.
- [ ] Re-measure and update the sizes in the topic registry.
- [ ] `make verify`.

### Phase 6: Reconcile the concepts docs

Both documents now ship, which raises the stakes: two contradicting glossaries in one
wheel. One sub-bead per divergence.

The rule this plan adopts: **where the two disagree about what Metaproc does, the
shipped concepts doc wins and the general doc adopts its term.
Where they disagree about the model, the general doc keeps its term and the concepts doc
gains an explicit pointer saying the concept is modeled but not implemented.**

- [ ] **roster.** The concepts doc says “Analysis-domain code uses *roster* as a
  synonym; the framework does not.”
  The general doc defines roster as a core term and uses it 24 times, and the design doc
  uses it 32 times including as a value in its own `role` enum.
  Decide one way and make all three agree.
- [ ] **task.** General: “the pivotal object in this model … the correct unit of
  scheduling, of failure, and of resume.”
  Concepts: “a runtime term used by state and log paths; it is not an authored process
  object.” Both are defensible about different layers; neither says so.
  State the layer each is talking about.
- [ ] **variant.** General makes it part of task identity.
  Concepts §4.1 makes it a run-level adapter selector.
  Reconcile or scope explicitly.
- [ ] **expansion, closure, generation.** Core objects in the general model, absent from
  the shipped glossary.
  Add pointers marking them modeled-but-not-implemented.
- [ ] **commit, fencing.** Same treatment.
  The general doc’s own deviations list already says Metaproc has “no single commit
  record covering a multi-output task”; the glossary does not mention the concept.
- [ ] Add a cross-reference header to each doc naming the other and saying which owns
  what.
- [ ] Re-read both docs end to end for divergences beyond these five and file any found.
- [ ] `make verify`.

## Testing Strategy

- `python -m devtools.check_links` after every move; it already gates `make verify` and
  is the safety net for a 105-reference rename.
- `python -m devtools.check_shipped_links` — new in Phase 1, and the only gate that can
  see a link which is valid in the repository and dead in the wheel.
- `make verify` at the end of each phase: public hygiene, distribution inspection, and
  the Agent Skill drift test all touch documentation paths.
- `uv run pytest tests/commands/test_help_command.py` for the registry refactor: topic
  listing, unknown-topic exit code 2, raw vs rendered output, and the new size column.
- `uv run pytest tests/test_locking_policy.py`, which hardcodes
  `src/metaproc/runpool/README.md`.
- `devtools/check_distribution.py` must show the twelve new documents present in both
  the wheel and the sdist — this plan *does* change the distribution payload, from
  13,742 to 75,844 words, and the check should assert the new set rather than tolerate
  it.
- Install the built wheel into a scratch environment and run `metaproc help` plus one
  moved topic, confirming the doc is served and its sibling links point at files that
  are actually present.
- Manual: browse README on the branch and confirm a reader reaches every first-party
  document without running the CLI.

## Rollout Plan

One pull request per phase, in order, each with green CI.

Phase 1 is a large diff that is almost entirely renames; review it with
`git log --follow` and `git diff -M` so the moves read as moves.
Phases 1 and 2 can land close together.
Phase 3 produces beads rather than a diff and may not need a pull request at all.
Phases 4, 5, and 6 each change prose that people rely on and deserve their own review
pass.

The wheel grows by roughly 470 KB of Markdown.
That is worth a CHANGELOG entry under a documentation heading, and worth saying plainly:
`metaproc help` becomes the complete documentation surface, not a three-manual subset.

## Open Questions

- Should the `arch-` prefix survive the move?
  It named a directory that no longer exists.
  Keeping it groups the seven component references in the topic listing; dropping it
  removes a distinction that may no longer mean anything to a reader.
  Deferred to Phase 3, when the set can be judged as a set.
- Do the runbooks belong in the wheel?
  This plan says no — they are procedures for this repository, not framework
  documentation. `credential-setup.runbook.md` is the awkward case, since the design doc
  links to it for auth setup and a downstream operator plausibly wants it.
- Should `docs/project/releases/` merge into `CHANGELOG.md` entirely?
  The two overlap, and the per-release files have almost no inbound links.
- Is `process-framework-concepts.md` still the right name once it ships next to
  `metaproc-concepts-and-principles.md`? Two documents named “concepts” in one directory
  is the naming problem Phase 6 has to answer in prose anyway.
- Is 75,844 words the right size for a shipped payload, or does Phase 5 need a target?
  A hard budget would force the cuts; an open-ended tighten may not.

## Outcome

Implemented 2026-08-27 across phases 1 through 6. Four things landed differently from
the plan above; each is recorded here rather than edited into the plan, so the reasoning
stays visible.

**Seventeen topics, not fifteen.** The shipped-link gate showed the architecture docs
linking to `credential-setup.runbook.md` twelve times and `cloud-dispatch.runbook.md`
five times. Both are framework-level rather than repository-level — `credential-setup`
contains no `make`, `uv run`, `devtools/`, or `pytest` reference at all — so they ship
as the `credentials` and `cloud-dispatch` topics instead of leaving seventeen references
pointing outside the package.

**The gate checks the package, not the docs directory.** The plan scoped the rule to
`src/metaproc/docs/`. Everything under `src/metaproc/` ships, at the same relative
offset in the wheel as in a checkout, so the accurate rule is the package boundary.
That also let `arch-runpool.md` link `../runpool/README.md`, which fixes the orphan the
audit found.

**178 violations, not 26.** The plan counted only the three original manuals.
The architecture docs link into source heavily: 77 of the findings were
`../../src/metaproc/*.py` references, most carrying `#L` line anchors.
Those became code spans — a line anchor is wrong the moment the file changes, and a path
an agent can read is worth more than a link it cannot follow.
116 became sibling links, 28 absolute URLs.

**`docs/` did not need emptying.** The plan moved `publishing.md`,
`performance-notes.md`, `memory-accounting-reference.md`, and
`agent-toolchain-bootstrap.md` under `docs/project/`. Once the framework documentation
moved into the package, `docs/` was left holding exactly the documents about working on
this repository, which is a coherent category — and `docs/project/` reads as “how the
project got here,” which those four are not.
Only `releases/` moved.

Phase 5 also went further than “cut the duplication”: §7 of the design doc, a downstream
analysis domain, moved to `docs/project/design/metaproc-analysis-profile.md`. Shipping
it would have put one consumer’s Predict/Retro/Mine/Learn vocabulary inside the
framework documentation of every downstream package, which AGENTS.md forbids and the
doc’s own backlog had already proposed fixing.
With §21 condensed, the design doc went from 19,926 words to 16,038.

Final shape: 17 topics, 74,812 words, `make verify` green (4,436 passed, 8 skipped),
both link gates clean, and all 17 documents asserted present in the wheel and the sdist.

## References

- Documentation audit, 2026-08-26. Figures re-measured against `d17b493`, the base this
  plan merges into.
- `docs/project/README.md` — the project-records rule this plan applies
- `devtools/check_links.py` — the gate that makes a rename of this size safe, and the
  one that cannot see wheel-dead links
- `src/metaproc/docs/__init__.py` — the topic wiring this plan rebuilds as a registry
