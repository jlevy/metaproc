# Review: Concepts and Principles — Structure and Rigor

**Review date:** 2026-04-25

**Primary doc under review:**
[`src/metaproc/docs/metaproc-concepts-and-principles.md`](../../src/metaproc/docs/metaproc-concepts-and-principles.md)

**Related docs:**
[`metaproc/docs/arch/arch-metaproc-core.md`](../arch/arch-metaproc-core.md),
[`metaproc/docs/metaproc-design-rev3-proposals.md`](../metaproc-design-rev3-proposals.md)

## Summary

The doc has a strong spine.
The three-axes framing (automation, exactness, structure) recurs throughout and
genuinely organizes the rest: it shows up in §1.3 (modes), §2 (boundary-first as an
exactness/structure tradeoff), and principle 23 (every primitive must advance one axis).
The ownership triad and the planes decomposition are mutually reinforcing without being
redundant. The eval grammar in §4.7 is tight enough that all later references stay
consistent with it.

Most of what follows is a critique of *structure*, not of *substance*. The
recommendations are reorderings, consolidations, and a few definitional tightenings.
None of them touch the underlying design.

The highest-impact changes:

1. Move vocabulary (§4) before architecture (§3); §3 currently uses terms it has not yet
   defined.
2. Tighten the principle list (§6): roughly a third of the 29 principles are either
   restatements of earlier definitions or operating philosophy aimed at a different
   audience.
3. Name the eval/gate runtime component.
   Principle 12 calls gates “engine-owned” but no such component is introduced anywhere.
4. Promote *produced-by vs produced-about* (currently a paragraph at the end of §4.6) to
   a first-class concept; it is the justification for filesystem-resident state and
   several other choices.

## What Holds Together Well

A short list, because the rest of this review is corrective and the spine deserves
explicit credit.

- **Three-axes framing as the spine.** §1.1 introduces automation, exactness, and
  structure; §1.2 frames them as bidirectional; §1.3 ties them to the four step modes;
  principle 23 makes them a test for new primitives.
  The doc returns to this scaffold often enough that a reader internalizes it.
- **Ownership triad → planes → principle 6.** §3.1 separates harness/processes/ agents
  by responsibility; §3.2 decomposes the harness side into planes; principle 6 restates
  the harness/agent split as an operating rule.
  The three layers add resolution rather than repeating.
- **Eval grammar in §4.7.** “Evaluators produce measurements; gates turn measurements
  into verdicts” is a tight enough sentence that every later use of those terms stays
  consistent. The framework-evals vs domain-evals split is the right cut.
- **Three loops as a typology.** §5.3’s A/B/C decomposition maps cleanly onto the
  run-record decomposition in §4.6. The Type B loop in particular is only legible
  because §4.6 distinguishes *produced-by* (live-only) from *produced-about*
  (re-computable).

## Structural Tensions

### 1. Vocabulary should come before architecture

§3 (Architecture) uses *artifact*, *plan*, *run pool*, *evaluator*, *gate*, *verdict*,
and *run record* as if they were defined, but their definitions live in §4. A first-time
reader either forward-references constantly or walks away with a hazy grasp of §3.

Two ways to fix this:

- **Swap §3 and §4.** Read order becomes vocabulary then architecture.
  §4 is reference-shaped (lookup); §3 is narrative-shaped (read once).
  Putting reference before narrative is the conventional ordering and works here.
- **Or, inline mini-definitions** for the small handful of terms §3 actually needs
  (artifact, run record, evaluator, gate).
  This preserves the current motivation→architecture flow at the cost of some
  redundancy.

The swap is the bigger win.

### 2. §1.3 fuses two ideas that should stay separate

§1.3 introduces the four step modes (`manual | agent | code | composite`) *and* the
three axes (automation, exactness, structure) in one section.
The two are not parallel: modes are an enumeration; axes are continuous.
A `manual` step is low-automation but can be high-exactness if its output is
schema-validated.
The section asserts that the modes give “a practical execution surface”
for the axes but does not actually map them.

Recommended change:

- Keep §1.3 as the axes-only summary, since axes are the spine.
- Move the four-mode enumeration to §4.5 (where it is already redefined).
- State the **completeness claim** for the four modes explicitly: *any step is one of
  these four because the work is done by* {nobody, code, an agent, another process}.
  Right now this is implicit and a reader can reasonably ask “why these four and not
  more?”

### 3. The principle list bundles four kinds of statement

The 29 numbered principles are not all the same kind of thing.
Going through them:

| Kind | Examples | What they really are |
| --- | --- | --- |
| Restatement of earlier definitions | 6 (harness owns…), 8 (shared state belongs to harness), 18 (four-mode uniformity) | Architecture facts that follow from §3, not principles |
| Genuine constraints | 1, 5, 7, 10, 12, 13, 14, 15, 17, 21, 22, 23 | Apply in new situations |
| Design choices framed as principles | 11 (framework evals always-on), 16 (producers don’t know consumers), 20 (meta-circularity) | Could have gone differently |
| Operating philosophy | 24-29 (token allocation, depth-first, etc.) | Advice for users of the framework, not properties of the framework |

§6.8 in particular reads as guidance for someone *building with* the framework, not
invariants of the framework itself.

Recommended changes:

- Move §6.8 to a separate doc, or relabel as “Operating philosophy” distinct from
  “Framework principles.”
- Demote restatement-style principles (6, 8, 18) to one-line consequences attached to
  whichever genuine principle they follow from.
  Principle 6 is a corollary of §3.1; principle 8 of principle 6; principle 18 of §3.2 +
  §4.7.
- After this pruning the count lands in the high teens, which is far more memorable.

### 4. Run record (§4.6) and per-step quality (§4.7) overlap on verdicts

§4.6 names the four sub-kinds of run record: trace, evaluations, annotations, verdict.
§4.7 then defines the eval grammar: evaluator, measurement, score, metric, verdict.
Verdict appears in both, with subtly different framing.
A reader has to hold two not-quite-identical definitions in their head.

Recommended change:

- §4.6 covers the **decomposition** (four sub-kinds, owners, lifecycles) without
  defining verdict in detail; it points forward to §4.7.
- §4.7 owns the eval grammar including verdict.
- The *produced-by vs produced-about* asymmetry at the end of §4.6 deserves its own
  short subsection (or a numbered principle).
  It is the justification for the filesystem-resident state model, immutable artifact
  storage, and the Type B loop’s feasibility, but right now it sits as the closing
  paragraph of §4.6 where a hurried reader will skim past it.

### 5. The eval/gate runtime component is unnamed

§4.7 and principle 12 say gates are “engine-owned.”
But no component called a “gate engine” or “evaluation runtime” is introduced anywhere:
not in §3.1 (ownership), not in §3.2 (planes), not in §4.5 (execution concepts).
The evaluation plane is described in §3.2.3 in terms of *what it produces*, not *what
runs the producers*.

This is the single biggest crispness gap in the architecture section.
The eval and gate plumbing is referenced extensively but its parts are never named.

Recommended change:

- Add to §3.2.3 (evaluation plane) a sentence naming the responsible component(s): who
  runs evaluators, who applies gates, what’s harness-owned vs plugin-supplied.
- Reflect that name in §4.5 alongside run pool and step mode.

### 6. Vantage uses meta-circularity, but the dependency is not made explicit

Meta-circularity and vantage are genuinely distinct principles, and the doc is right to
keep them in separate subsections.
They are not the same idea and should not be merged.
But the doc presents them as parallel without naming the directional dependency between
them, which the Type C loop quietly relies on.

The two principles, stated crisply:

- **Meta-circularity** is a property of the *format*: a process spec is itself a
  readable, writable artifact in the same format the framework consumes, so a process
  can read, evaluate, or rewrite another process (or itself).
  This is the same sense in which a compiler is meta-circular when it is written in the
  language it compiles.
  Meta-circularity is a property of representation, not of any particular loop.
- **Vantage** is a discipline applied to *steps in a self-improvement loop*: it
  distinguishes foresight (forward-looking, must not see outcomes), hindsight
  (backward-looking, has full information), and retrospective (compares the two and
  produces improvements).
  Vantage is about temporal honesty in evaluation, not about the format.

The connection is one-directional: a retrospective step *uses* meta-circularity, because
the improvement it emits is a new process spec, and that is only meaningful because
process specs are first-class readable/writable artifacts.
The reverse is not true: meta-circularity is well-defined without vantage.
Validation, dry-runs, and process-reading tools are meta-circular without ever touching
the foresight/hindsight distinction.

§5.3.3 quietly relies on this dependency when it says the retrospective output *is* the
input to the next iteration of the foresight process.
But neither §5.1 nor §5.2 names the dependency, so a reader has to reconstruct it.

Recommended change:

- Keep §5.1 and §5.2 as distinct subsections.
- At the end of §5.2, add one sentence: vantage discipline becomes a process-level
  capability (rather than a notebook practice) only because meta-circularity makes
  retrospective output expressible as a new process spec.
- Consider an analogue line at the start of §5.3.3 reminding the reader that this loop
  is the place where the two principles combine.

### 7. Loop preconditions are scattered across §5

§5.4 names two preconditions for any loop (bounded lever space, loop reliability).
But others are stated inside the per-loop subsections:

- Type A: framework evals held constant (§4.7, end).
- Type B: durable artifacts and versioned evaluators (§5.3.2).
- Type C: meta-circularity and retrospective vantage (§5.3.3).

Consolidating these into one table at the top of §5.4 would make the design discipline
of the loops legible at a glance:

| Loop | Holds constant | Varies | Preconditions |
| --- | --- | --- | --- |
| A | inputs, eval | step (prompt/code/model) | bounded lever space, framework evals constant, loop reliability |
| B | step | evaluator | durable artifacts, versioned evals |
| C | (varies) | process structure | meta-circularity, retrospective vantage, spec-as-artifact |

### 8. The items / map / fan-out terminology overloads “map”

§4.2 acknowledges that *map* is both a YAML map (data shape) and a higher-order
operation (apply to each).
The doc handles this by noting “structurally a map; functionally the element being
mapped over,” which is honest but asks the reader to context-switch.

Two small refinements:

- In concept-level prose, use **fan-out over items** consistently for the operation, and
  reserve *map* for the data shape.
  The code can keep `map`; this is a doc convention.
- The hierarchy (item → map item → items file) would benefit from a one-paragraph
  example: a concrete `tickers.md` with two rows, showing what an item is and what a map
  item is when the file is consumed by a fan-out step.

### 9. §3.4 (abstraction profiles) lands too lightly for the weight of its claim

Three paragraphs introduce core, execution, and application profiles, with the strong
claim that “application examples never leak back into core schema.”
Yet nothing in §3.4 says *how* application profiles register their conventions, or what
the contract is. The plugin protocol is gestured at but lives in
[arch-metaproc-core.md](../arch/arch-metaproc-core.md).

Recommended change:

- Make the punt explicit: one sentence pointing to the relevant section of the design
  doc.
- Or, if the contract has a one-paragraph summary, include it in §3.4. The no-leak claim
  is too strong to leave without a mechanism.

## Gaps

### Failure modes and limits are absent

The doc is uniformly advocacy.
There is no “when not to use Metaproc” or “known weaknesses.”
Plausible candidates worth naming:

- **Step granularity that’s too fine.** Whole-app artifact at every step makes the file
  boundary pure overhead.
- **Workflows where messages-as-context dominate.** Interactive chat, REPL-shaped
  exploration, anything where the conversational state *is* the work product.
- **High-frequency small ops.** The subprocess boundary cost dominates if a step runs in
  milliseconds.

A short Limits subsection (one paragraph) at the end of §2 or §6 calibrates readers and
makes the framework’s positioning more honest.
The agent-SDK comparison in §7 already implies this; it deserves to be said directly.

### Composite mode is under-specified at the concepts level

§4.5 defines `composite` as “delegates to a child `*.process.md`” but does not say:

- What the parent step’s *artifact* is when the child is itself a fan-out.
- How parent fan-out and child fan-out compose.
- How a child failure or partial-fail surfaces to the parent verdict.

Even one sentence per question would close the gap.
These are concept-level questions, not implementation questions, and the answers shape
how a reader thinks about composability.

### Variant definition is entangled with the current implementation

§4.1 defines variant as “one set of metaparameters for a run; *currently*, in the
process spec’s `adapters` map, variant is the name of the adapter…” The “currently”
hedge mixes the concept with its present implementation.
A reader cannot tell which parts are the durable concept and which will move.

Recommended change:

- Lead with the implementation-free concept: a variant is one named choice along the
  metaparameter axes for a comparison set.
- Follow with one sentence noting that the present implementation exposes only adapter
  selection as a variant; richer variants are deferred per
  [metaproc-design-rev3-proposals.md](../metaproc-design-rev3-proposals.md) P7.

### Cross-plane interaction (§3.3) lists allowed flows but not forbidden ones

§3.3 enumerates representative cross-plane flows.
The complement is missing: which flows are *not* allowed?
Principle 16 ("producers do not know about consumers") is the inverse rule, but it is
stated 30 sections later.
Adding the inverse to §3.3 itself, with one or two examples of “this is what makes the
planes a real decomposition” (e.g., the execution plane never reads observation
surfaces), would make the planes feel like constraints rather than labels.

## Smaller Refinements

These are minor enough to bundle.

- **§1.3:** state the four-mode completeness claim explicitly.
- **§3.1 → §3.2:** add one sentence naming who owns the evaluation plane.
  Currently the harness is named in §3.1 but evaluators feel ownerless.
- **§4.2:** drop *map* as the operation name in concepts prose; keep *fan-out* only.
- **§4.1 (variant):** lead with concept, follow with current-implementation note.
- **§6.7 problem taxonomy:** “context / question / structural” needs a one-sentence
  definition for each bucket.
  Right now the three categories are named without being defined.
- **Principle 11 and principle 15:** overlap on the always-on framework / typed-core
  story. Either merge or cross-reference tightly.
- **§7 detailed agent-SDK comparison table:** rich content, heavy in a concepts doc.
  Could move to a separate “design context” appendix and leave only the one-sentence
  positioning per analogue in §7.

## Restructuring Proposal, Ranked

1. **Swap §3 and §4** so vocabulary precedes architecture.
2. **Promote *produced-by vs produced-about*** out of the §4.6 closing paragraph.
3. **Add the loop-preconditions table** to the top of §5.4.
4. **Name the eval/gate runtime component** in §3.2.3 and §4.5.
5. **Split §6** into framework principles (~18) and operating philosophy (24-29); demote
   restatement principles (6, 8, 18) into consequences of the genuine ones.
6. **Add a Limits subsection** at the end of §2 or §6.
7. **Tighten §5.1 ↔ §5.2 coupling** with one opening sentence in §5.2.
8. **Move §1.3 four-mode enumeration** into §4.5 and keep §1.3 axes-only.

The first four are the highest leverage.
The rest are tidying.

## What This Critique Is Not

It is not a critique of the design itself.
The harness/agent split, file-artifact contracts, deterministic orchestration,
run-record decomposition, three-loop typology, and meta-circularity are all coherent and
(where checked against [arch-metaproc-core.md](../arch/arch-metaproc-core.md))
consistent with what the system actually does.
The recommendations above are about how the doc presents that design, not what it says.

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
