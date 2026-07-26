# Review: Layers, Improvement Loops, and Comparison Surfaces

**Review date:** 2026-04-25

**Primary doc under review:**
[`src/metaproc/docs/metaproc-concepts-and-principles.md`](../../src/metaproc/docs/metaproc-concepts-and-principles.md)

**Related reviews:**
[review-2026-04-25-concepts-and-principles-structure.md](review-2026-04-25-concepts-and-principles-structure.md),
[review-2026-04-25-concepts-and-principles-substrate-alignment.md](review-2026-04-25-concepts-and-principles-substrate-alignment.md)

**Source material:** `example_workflow/layers/README.md`,
[example_workflow/docs/improvement-layers.md](../metaproc-design-rev3-proposals.md),
[example_workflow/knowledge-base/README.md](../metaproc-design-rev3-proposals.md),
`example_workflow/process/layer-experiment/layer-experiment.process.md`,
`example_workflow/layers/driver-identification/`

## Summary

Three observations, one unifying claim.

1. The codebase uses “layer” in two genuinely different senses: the *improvement layers*
   of [improvement-layers.md](../metaproc-design-rev3-proposals.md) (artifact / process
   / framework), and the *reasoning layers* of `example_workflow/layers/`
   (driver-identification, scenario-planning, etc.). These are different concepts.
2. A reasoning layer is functionally a step plus the evaluation harness that makes it
   improvable. The substrate provides the step; the reasoning layer adds the comparison
   surface around it.
3. The same comparison-surface shape recurs at every improvement layer, with
   progressively less audit discipline as you move outward.
   The reasoning-layer system has the most mature audit trail in the codebase; Layer 1
   has partial structure; Layer 2 is mostly informal; Layer 3 is purely informal.

The unifying claim: **comparison surface** is the abstraction the concepts doc is
missing. Naming it would give the reasoning-layer system a substrate-aligned vocabulary,
would let the outer improvement layers borrow the discipline the inner one has already
proven, and would clarify the line between substrate and authored patterns.

The discipline being borrowed is mostly auditability: who decided what changed, on what
evidence, against what frozen baseline.
The reasoning layer makes this mechanical; the outer layers do it by hand or not at all.

## 1. Two Senses of “Layer”

### Sense A: Reasoning Layer

From `example_workflow/layers/README.md:21-36`:

> A layer is one reasoning responsibility in the larger prediction workflow.
> Examples: driver-identification, scenario-planning, prediction-translation,
> tradeability-rejector.
> A layer owns: the input boundary it reads, the output artifact it writes, the default
> evaluator or label set, the architecture variants that can run the layer.

A reasoning layer is a *sub-step* within a workflow, scoped narrowly enough that it can
be evaluated and iterated on without rerunning the entire prediction packet.

### Sense B: Improvement Layer

From [improvement-layers.md:7-82](../metaproc-design-rev3-proposals.md):

| Layer | Scope | What’s being improved |
| --- | --- | --- |
| 1: Predict-Retro-Learn | individual predictions and the feedback cycle | the artifact (a prediction) |
| 2: Earnings Process Improvement | structure, tooling, conventions | the workflow that produces predictions |
| 3: Meta-Framework | the general pattern of agent-driven process improvement | the framework substrate itself |

An improvement layer is a *scale at which improvement happens*, not a piece of a
workflow. The three layers nest: Layer 1 generates the experience that Layer 2 learns
from; Layer 2 generates the experience that Layer 3 learns from.

### These Are Different Things

Sense A names a *piece of a workflow*; Sense B names a *scale of object being improved*.
The word “layer” reads naturally in both contexts because both involve stacking, but
they stack along different axes.

A reasoning layer is one component of a prediction.
An improvement layer is one scope at which to run an optimization loop.

## 2. Is a Reasoning Layer Just a Step?

Mostly yes, but with critical additions.
The substrate already provides the step; a reasoning layer is a step that has been
*promoted to a comparison surface* by adding evaluation infrastructure around it.

| What a step has today | What a reasoning layer adds on top |
| --- | --- |
| Input declarations | Frozen input cohort (versioned datapoints) |
| Output declarations | Output kept stable across architecture variants |
| Mode (agent / code / manual / composite) | Architecture (versioned alternatives) |
| Adapter selection | Hidden ground truth (label set, versioned independently) |
| (none) | Judgment recipe (scoring profile, versioned independently) |
| (none) | Promotion filter (which rows count for gating) |
| (none) | Tiered verdict (pass / warn / fail) |
| (none) | Audit trail (frozen_from provenance, versioning of every axis) |

The left column is substrate.
The right column is authored evaluation infrastructure that the layer system has built
on top of metaproc.

The clean naming: **a reasoning layer is a step with a comparison surface attached.** A
step is the unit of execution; a comparison surface is the unit of improvement.

This matters for substrate design.
Today the substrate has step but not comparison surface.
The concepts doc talks about Type A/B/C loops abstractly, but the mechanics that make
them work in practice (frozen cohorts, versioned label sets, versioned scoring profiles,
promotion filters, provenance) are entirely authored on top of file conventions.

## 3. The Comparison Surface Abstraction

Every improvement layer needs the same shape to be auditable:

- **Scope:** what’s being improved.
- **Frozen substrate:** what’s held constant during the comparison (cohort, ground
  truth, recipe).
- **Variant set:** what’s varied (architectures, prompts, models, spec versions).
- **Evaluation:** how each variant is scored.
- **Decision:** which variant wins, and by what method (unanimous, reviewed, threshold,
  operator override).
- **Provenance:** the audit trail tying the decision back to the substrate, the
  variants, and the evaluation.

The same shape instantiates at every scale:

|  | Reasoning layer (driver-id) | Improvement Layer 1 | Improvement Layer 2 | Improvement Layer 3 |
| --- | --- | --- | --- | --- |
| Scope | one reasoning task | one prediction form | one process spec | one framework primitive |
| Frozen | datapoint cohort + labels + rubric | ticker universe + retro process | framework + domain conventions | implementation + usage patterns |
| Varied | architectures | form versions | spec variants | primitive variants |
| Evaluation | per-row pass/warn/fail | retro outcomes vs predictions | operational friction signals | generalization across domains |
| Decision | aggregate over promotion-eligible | learn-review form update | improve-review spec update | framework retrospective |
| Provenance | frozen_from, label-set version, scoring profile id | run id + form version | spec version | framework version |

The substrate provides files, fan-out, and state.
The comparison surface composes those into something improvable.
Today this composition is authored per process family.

## 4. Audit-Trail Maturity Decreases Outward

The reasoning-layer system at the bottom of the stack is the most disciplined; the outer
layers progressively less so.

**Reasoning layer (driver-id):** mature audit trail.

- Per-datapoint `frozen_from` provenance.
- Independently versioned label sets, scoring profiles, datasets, architectures.
- Three-tier verdicts (pass / warn / fail).
- Promotion filters: rows tagged `label_status: approved` and
  `evidence_sufficiency: strong` drive gating; thinner rows stay as diagnostics.
- Per-row evaluation index that ties output, label, and judgment together.

**Improvement Layer 1 (predict-retro-learn):** partial audit trail.

- Predictions are timestamped per run; retros link back to predictions; learn reviews
  aggregate retros into form proposals.
- But form-vs-form comparison is informal.
  The question “did v11 beat v10 on the same ticker universe under the same retro
  definition?” is not currently a structured query.
- Form changes have rationale recorded in changelogs; the link from a specific retro
  finding to a specific form change is not always traceable.

**Improvement Layer 2 (process improvement):** minimal audit trail.

- `process/improve/improve.process.md` exists as a scaffold for human-triggered
  review/proposal.
- Most process changes happen ad-hoc through PR review without a structured before/after
  comparison.
- “Did this process change reduce friction?”
  is not a metric anyone runs.

**Improvement Layer 3 (meta-framework):** purely informal.

- Framework changes happen via PR culture and design memos.
- There is no comparison surface across domains because there is currently one domain.

The pattern: comparison-surface discipline is well-developed where it lives in code
(reasoning-layer system), partially developed where it lives in conventions (Layer 1),
and informal where it lives in PR culture (Layers 2 and 3).

This isn’t a criticism of the outer layers.
They are less mature because they have less recurrence.
But the discipline shown by the reasoning-layer system is borrowable.
The unifying abstraction lets the outer layers adopt the same mechanical audit trail
when they recur enough to deserve it.

## 5. What the Reasoning Layer Teaches the Eval Grammar

The §4.7 eval grammar (`evaluator → measurement → metric / gate → verdict`) is the
substrate skeleton. Real evaluators in the layer system instantiate it with:

- **Label set:** versioned, scorer-only, separate file.
  Encodes acceptable answers, evidence sufficiency, ambiguity, and `label_status` per
  row. See `smoke-v1-labels-v1.yml`.
- **Scoring profile:** versioned, separate file.
  Encodes match semantics (`top1_match`, `recall_at_3`), promotion filter, judgment
  fields, aggregation cohorts.
  See [scoring-profile.yml](../metaproc-design-rev3-proposals.md).
- **Promotion filter:** sits between metric and verdict.
  Restricts gating to rows where the label is trustworthy enough.
- **Tiered verdicts:** `pass | warn | fail`, not `pass | fail | force_advanced`. The
  `warn` tier ("directionally useful but not a clean top-1") drives different
  next-experiment decisions than a clean fail.
- **Aggregate-by cohorts:** the same evaluation can be reported overall, per ticker, per
  evidence-sufficiency band.

Independent versioning of label set and scoring profile is what makes the Type B loop
real: you can re-score frozen layer outputs against a new scoring profile without
rerunning the layer.

The honest reframing for §4.7: the substrate vocabulary is the skeleton; routine
authored evaluators add label set, scoring profile, promotion filter, and tiered
verdicts. The earnings layer system is the canonical example.

## 6. Vantage Is Enforced by Directory Layout, Not by Substrate

§5.2 names foresight, hindsight, and retrospective vantages but does not say how they
are enforced. The layer system shows the answer: *vantage is a directory and roster
discipline, not a substrate primitive*.

Per `layers/README.md:114-117`:

> Keep labels hidden from the runner.
> In v1 this is enforced by dataset shape and prompt/process discipline: labels live
> outside `input/`, runner rosters do not include label paths, and leakage QA checks
> runner-facing files.
> It is not yet a hard filesystem sandbox; add sandboxing before treating adversarial
> label access as impossible.

That paragraph is the explicit acknowledgment that the discipline is operational, not
enforced. The concepts doc should:

- Name “vantage by directory and roster discipline” as today’s operational pattern.
- Mark “scorer-only output declaration with read-scope enforcement” as a candidate
  future substrate primitive that graduates if a second process family needs it.

The label-set / runner separation is also the cleanest in-code expression of foresight /
hindsight separation in the codebase.
Dataset construction is the hindsight process; layer experiment is the foresight
process; the directory layout is the wall between them.

## 7. Decision Records Are a Family

The substrate-alignment review introduced *decision records* as a category, with
operator overrides as the example.
The knowledge-base shows this category has at least four members:

| Decision kind | Concrete instance | Provenance fields |
| --- | --- | --- |
| Override | operator unblocks a downstream step despite an upstream failure | actor, reason, scope |
| Promotion | knowledge-base record passes from candidate to authoritative | source run, source variant, method (`unanimous \| reviewed \| auto`) |
| Selection | one variant wins among multiple in a divergent comparison | reviewer, accepted variant, rationale |
| Rejection | divergent-unresolved candidate is dropped | rule (`bad data is worse than no data`) |

All four share: explicit actor attribution, recorded rationale, separable from the
original execution result, and entry in an audit trail.
The concepts doc should treat decision records as a family with these subkinds rather
than as a single override-shaped primitive.

## 8. The Five Independently Versioned Axes

A reasoning-layer comparison surface decomposes into five axes that are all versioned
independently:

| Axis | Concrete artifact in driver-id |
| --- | --- |
| Frozen input cohort | `datapoints/{id}/input/` plus `dataset.yml` |
| Reasoning approach | `architectures/{name}/runbook.md` |
| Ground truth | `labels/{dataset}-labels-v1.yml` |
| Judgment recipe | `evaluator/scoring-profile.yml` |
| Per-output judgment | `evaluation-index.md` rows |

Independence matters for the optimization loops:

- **Type A** = freeze cohort + labels + scoring profile, vary architecture.
- **Type B** = freeze cohort + architecture, vary scoring profile (or label set
  version).
- **Combined** = the comparison surface lets you do A while preserving the option to
  come back later for B.

The concepts doc currently says “evaluations are versioned and non-destructive”
(principle 13) without naming the mechanism.
The mechanism is *separate file paths with version suffixes for label sets and scoring
profiles, plus a `provenance` block that records which versions were in effect for each
run*. That deserves explicit naming.

## 9. What Belongs in the Substrate, What Stays Authored

Substrate today (per the new §1.4):

- File artifacts at boundaries.
- Steps with modes.
- Fan-out and dependency graph.
- State, attempts, results, events.
- Adapter selection and runtime dispatch.

Recurring authored patterns ready for naming, not yet for substrate primitives:

- Comparison surface (the abstraction itself).
- Frozen input cohort.
- Five-axis decomposition (cohort / architecture / labels / scoring / per-row
  evaluation).
- Vantage by directory and roster discipline.
- Three-tier verdicts.
- Promotion filters and provenance blocks.
- Decision records (override / promotion / selection / rejection).

Probably never substrate primitives:

- Domain evaluation semantics (rubric content).
- Specific label schemas.
- Domain promotion rules.

The comparison-surface concept itself: belongs in the concepts doc as the canonical
authored pattern that recurs at every improvement layer.
Not a substrate primitive yet because it has only recurred in one domain (earnings) so
far. Per principle 21, it graduates when a second domain shows the same shape.

## 10. Naming Recommendations

The two senses of “layer” coexist naturally in casual speech but trip over each other in
technical writing. Three resolution options:

1. **Qualifiers everywhere.** Use *improvement layer* and *reasoning layer* wherever
   ambiguity is possible.
   No path renames, no vocabulary churn.
2. **Rename reasoning-layer to step-surface** (or similar).
   Keeps “layer” for the improvement-scale sense.
   Invalidates `example_workflow/layers/` paths.
3. **Rename improvement-layers to improvement-scopes** (or scales).
   Keeps “layer” for the concrete in-code sense.
   Invalidates Layer 1 / 2 / 3 vocabulary that is already established in design
   conversations.

Recommendation: Option 1 (qualifiers), with **comparison surface** introduced as the
underlying abstraction.
The two layer-flavored names then become specific instantiations of comparison surfaces
at different scales, and the abstraction name is unambiguous.

## 11. Suggested Edits to the Concepts Doc

Carrying over from the two prior reviews and adding what this review surfaces:

**A. Add a Comparison Surface section** to §5 (Optimization Loops).
Describe the six elements (scope, frozen substrate, variant set, evaluation, decision,
provenance) and reference the layer system as the most mature instantiation.
State that the substrate provides files, fan-out, and state; the comparison surface is
an authored composition.

**B. Recognize improvement layers alongside Type A/B/C.** The three improvement layers
and the three loop types are not the same axis.
Loops describe *what kind of variation* (step output, evaluator, workflow shape);
improvement layers describe *what scale of object* (artifact, process, framework).
A short subsection clarifying the orthogonality would prevent readers from conflating
them.

**C. Expand the §4.7 eval grammar** to acknowledge label set, scoring profile, promotion
filter, and tiered verdicts as the routine authored instantiation of the substrate
skeleton.

**D. Name vantage-by-directory** as today’s operational pattern in §5.2, with
“scorer-only output declaration” as a candidate future substrate primitive.

**E. Expand decision records** into a family (override, promotion, selection, rejection)
with shared structure.

**F. Reframe meta-circularity around data-circularity.** The recurring pattern is
*dataset-construction → layer-experiment → retrospective*, not direct spec rewriting.
Spec-rewriting is rare; data-circularity is the everyday form.

**G. Architecture vs adapter.** Architecture is a compound (prompt structure, reasoning
approach, model, possibly multi-step arbitration).
Adapter is a thinner concept (CLI wiring + auth).
The variant decomposition should distinguish them.

**H. Frozen input cohort.** Add to the §1.4 substrate-boundary list of authored
patterns.
It is the operational form of “artifacts at boundaries” and is stable enough to
name explicitly, even before substrate-level support.

## 12. Open Questions

These don’t have obvious answers; recording them here for the next pass.

- **Should the substrate provide a scorer-only output declaration primitive?** Today,
  label hiding is roster discipline.
  A mechanical sandbox would prevent whole classes of leakage bugs but also requires a
  real read-scope enforcer.
- **Should comparison surfaces have a common manifest format?** Today each authored
  process invents its own (`datapoint.yml`, `dataset.yml`, `scoring-profile.yml`, etc.).
  A shared manifest could let the substrate render cross-architecture comparison reports
  automatically.
- **Should reasoning-layers become a metaproc plugin?** The vocabulary (layer,
  datapoint, dataset, architecture, label set, scoring profile) has stabilized enough
  that a plugin registering these as plugin-scoped concepts may be earned soon.
  Plugin scope keeps it out of the core substrate while still giving it first-class
  support.
- **What does Layer 3 evaluation look like with a second domain?** Layer 3 improvement
  is currently informal because there is one domain.
  Onboarding a second domain to metaproc is the natural moment to discover what
  “framework generalizes well” actually means.
- **How do retrospective processes (the Type C loop) get a comparison surface?** The
  substrate review correctly says retrospective DSLs should remain authored patterns
  until they recur. But the comparison surface for “did this process change reduce
  friction?” needs at least one worked example before it can be generalized.

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
