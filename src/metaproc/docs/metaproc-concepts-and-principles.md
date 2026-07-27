---
title: "Metaproc: Concepts and Principles"
description: Concepts and motivation for Metaproc, including vocabulary, architectural planes, optimization loops, and design principles.
---
# Metaproc: Concepts and Principles

Related docs: [developer guide](metaproc-developer-guide.md) (extending metaproc) ·
[operator reference](metaproc-operator-reference.md) (runtime CLI). Served at runtime
via `metaproc help concepts`.

## Overview

**Metaproc is a framework for complex, repeatable, and self-improving agent processes.**
It puts deterministic orchestration, validation, state, observability, and resume around
flexible combinations of code and agent steps.
File artifacts are the step boundary, which makes workflows measurable, cacheable,
resumable, inspectable, and improvable.

What’s distinctive:

- **Simple but extensible format:** Processes are just Markdown and YAML. But arbitrary
  use of CLIs or additional code is allowed.
- **Boundary-first:** Structure lives at step boundaries (file artifacts); step
  interiors are unconstrained.
- **Gradual structure and precision:** A process can be as simple and unstructured as an
  English description or as exact and structured as pure code—or any combination.
- **Arbitrarily powerful steps:** Each step can be code, arbitrary coding agents defined
  in code or language, or other processes.
- **Meta-circularity:** Processes can improve other processes.
  Process definitions are a concise format of YAML and English (Markdown).

The last point is important: *Processes can read, evaluate, and improve other processes
or themselves.* Supporting meta-processes is essential for self-improving loops, like
auto-research loops.

Implementation details, including the `.process.md` format, runtime artifacts, CLI
commands, adapter wire formats, plugin protocol, run pool internals, cloud execution,
and robustness subsystems, live in
[arch-metaproc-core.md](../../../docs/arch/arch-metaproc-core.md).

## 1. Motivation

### 1.1 Automation, exactness, and structure

Agents are a software automation technology.
But even before LLMs and agents, there has always been a *spectrum* of levels of
automation when it comes using software in knowledge work:

- **Fully manual processes:** Every part of the work is done by a human.
- **Fully automated processes:** Every part of the work is done by software, without any
  human oversight.

Of course, in reality, processes in knowledge work lie in between the extremes: they
have parts that run fully unattended and parts that require human contribution, review,
QA, or oversight.

The rise of agents has let us automate things that were previously considered human
tasks. However, traditionally software has been *exact*, like precise calculations over
structured data, such as relational databases.

This is no longer true.
We can now automate things involving natural language, where both the data and the
instructions might be in English and inherently ambiguous in both meaning and structure.

It has become necessary to begin thinking about two other spectrums in the context of
automation: **exactness** and **structure**.

In software systems, reliability is a defining characteristic.
A system designed for “one nine” or “two nines” of reliability (90% or 99% reliable) is
very different than one designed for “five nines” or “six nines” (99.999% or 99.9999%).
We’ll call this “exactness”:

- **Exact processes:** Pure code (like Python, TypeScript, or Rust) that are efficient
  and execute with high reliability.
- **Inexact processes:** Natural language instructions followed by humans or agents that
  can be flexible but are usually not as reliable.

Of course, this is a spectrum as well.
It’s important to notice that we treat exact and inexact processes very differently
because exact processes can be combined and composed to yield other exact processes.
Inexact processes usually cannot be combined many times because the reliability becomes
so low it renders the combination useless (unless you add *another* mechanism to
validate results).

Exactness reflects the precision of the process itself, but we also have to think about
the data that’s being processed at different points on the structure spectrum:

- **Structured data and workflows:** The most structured workflows are defined in code
  exclusively and work over data with tight schemas.
  Many traditional workflow or ETL systems are highly structured.
- **Unstructured data and workflows:** The least structured workflows are essentially
  managed completely in English by coding it by agents.

Of course, these are not clear-cut distinctions.
It helps to think these as three axes:

- **Automation axis:** human-performed ↔ human-overseen ↔ fully harness-driven.
  Convert LLM prompts to code and vice versa.
  Or add or remove a manual gate.
- **Exactness axis:** agent/NL ↔ typed contracts ↔ deterministic code.
  Harden a step to code when the logic is well-understood; convert back to an agent when
  flexibility matters more than determinism.
- **Structure axis:** freeform text files ↔ explicit file formats ↔ fully typed,
  validated schemas for all data.
  Add structure as repeated patterns prove they deserve it; relax it when a step needs
  room to explore. The repo’s practical artifact guidance is
  [softschema-guidelines.md](../../../docs/conventions.md#file-format-policy).

### 1.2 Gradual automation, exactness, and structure

Current popular agent frameworks (LangGraph, Mastra) or YAML DSLs (GitHub Actions, Argo)
lean toward the structured end at the overall workflow level but often contain
unstructured elements like LLM calls.
Coding agents like Claude Code, Codex also contain structures (like code or data files
in known schemas) but blend it with unstructured conversations and prompts.

A key goal with Metaproc is to recognize that processes at any given point have varying
levels of automation, exactness, and structure.

It is unrealistic to assume that something will be fully automated and exact up front,
and often you wish to trade off the benefits.
For example, making a process less exact but more flexible, or making a process
unstructured, at first described only in English.
But then, as efficiency and reliability become more important, later adding structure.

- **Gradual automation:** Processes that are fully or partly manual can be gradually
  automated, piece by piece, or have manual gates added when oversight is needed.
- **Gradual exactness:** Steps are interchangeable between code and agents.
  A process can begin in natural language then evolve into more precise steps, some in
  code, some in agent tools, some still in natural language, or move back toward agents
  when flexibility matters more than determinism.
- **Gradual structure:** Coding agents are first-class step executors.
  A step can be fully unconstrained with just inputs and outputs specified, fully
  constrained as conventional code, or partly constrained, as a sequence of sub-steps.

And because the process definition is itself a file, it is amenable to the same
processes of improvement.
This is the foundation of **meta-circularity** (§5.1).

### 1.3 The four step modes and the three axes

The framework supports four step modes, built on three work semantics:

| Mode | Semantic | Typical use |
| --- | --- | --- |
| `manual` | human does the work | approval, gating, exception handling |
| `agent` | coding agent does the work | research, synthesis, open-ended execution |
| `code` | deterministic code does the work | parsing, validation, exact transforms |
| `composite` | delegates to another process | recursion and process composition |

The four modes give a practical execution surface for:

- human ↔ agent transitions
- agent ↔ code transitions
- approval and review gates
- recursive process trees

### 1.4 Substrate boundary

Metaproc is a process *substrate*: it provides structure, planning, execution, and
state, and leaves domain logic to authored processes.
The substrate is intentionally small.

What the substrate provides:

- **A format for process definitions:** steps, dependencies, modes, variables, IO specs,
  fan-out declarations, retry policy, and adapter configuration, expressed in
  `*.process.md` files with YAML frontmatter and Markdown body.
- **Planning and validation:** resolved steps, adapters, output roots, dependency
  references, and fan-out inputs.
- **Runtime execution:** local and cloud dispatch, state files, attempt and result
  records, logs, process events, resume behavior, output validation, and write-boundary
  checks.
- **Extension points:** schemas, envelopes, terminal statuses, process rules, compare
  defaults, visualizers, adapter variants, and plugin handlers.

The substrate does *not* offer domain-specific logic or even an agentic loop.
The usual agentic loops are handled *within* steps, using coding agents (like Claude
Code, Codex, or Pi) or custom code.

## 2. Boundary-First Architecture

A central concept of Metaproc is that **structure is imposed at step boundaries, but
imposes no constraints on the interiors of steps.** What goes in, what comes out, and
what depends on what: that’s where schemas, validation, and contracts live.
Inside a step, there is complete freedom.

Files are the boundary because they are:

- universal across tools
- visible to humans and agents
- resumable
- versionable
- natural for coding agents

Much like with traditional Makefiles, file boundaries make any stage **snapshottable,
rerunnable, and inspectable**, which is what allows mid-run debugging, optimization of
steps in isolation, and resumption of processed data with revised processes.

The framework is Makefile-like in that outputs and dependencies matter, but differs from
a Makefile in four ways:

- artifacts are typed
- steps can be full coding agents
- completion is based on validation, not just file presence
- status and attempt state are emitted as structured runtime artifacts

**The adoption path is boundary-first.** Start by making the file handoff between steps
explicit; do not start by encoding every branch, loop, or validator:

1. identify the process directory
2. identify the step inputs and outputs
3. declare the outer structure in the node’s `<node>.process.md`
4. keep the runbook freeform until a repeated pattern proves it deserves structure

Simple is simple; complex is possible: the framework supports flexible workflows and
increasingly powerful agentic reasoning within individual steps.

## 3. Architecture

### 3.1 Ownership roles

Three responsibilities, three owners.
Each owns its slice and never reaches into the others.

- **Harness:** orchestration, contracts, state, validation, retry, resume, publication,
  log lifecycle, resource monitoring.
  Deterministic Python code, not an agent, because orchestration is too important to be
  subject to context-window limits or LLM drift.
- **Processes:** domain logic: item parsing, path conventions, enrichment rules,
  filtering, runbooks.
  Authored as `.process.md` files.
- **Agents:** in-step reasoning only.
  Whatever the agent does internally is its own business; the contract is the file
  artifacts at the boundary.

### 3.2 Architectural planes

Planes are conceptual roles that the framework plays.

#### 3.2.1 Control plane

Harness orchestration: DAG walking, dispatch, state transitions, resume, retry,
publication. Decides *what runs and when*. Produces authoritative state and dispatches
work to the execution plane.

#### 3.2.2 Execution plane

Work performed inside a step: agents via adapters, code handlers, manual
acknowledgments. Produces artifacts and emits trace.
The adapter contract is the boundary between the harness and the agent runtime.

#### 3.2.3 Evaluation plane

Judgment applied to a completed run.
Evaluators consume artifacts and trace and produce measurements; gates turn measurements
into verdicts; overrides are operator-supplied verdicts.
Decides *whether work was good*.

#### 3.2.4 Observation plane

Read-only views over everything the three producer planes wrote.
Produces nothing the producer planes haven’t already recorded.
Splits into two surfaces with different form factors but the same underlying data:

- **Monitoring surface (analytics):** text-and-table renderings oriented toward
  operational use: run status, log rollups, cost/usage reports, error surfacing, alerts,
  comparison tables, resource reports.
- **Visualization surface:** pixel-and-chart renderings oriented toward visual
  inspection: browser UI, time-series charts, DAG diagrams, static HTML/SVG renders.

The same events, status, measurements, and verdicts feed both surfaces.
Adding a dashboard, swapping the browser for Grafana, or piping events into a different
store does not touch how steps run.

#### 3.2.5 Interaction plane

An operator or another agent can *use* Metaproc from the outside: initiating runs,
inspecting state, unblocking gates, reviewing results, improving specs, and running
experiments.

Uses on the interaction plane:

- **Invocation:** running processes and steps.
- **Inspection:** querying plan, status, dependencies, validation results.
- **Intervention:** overrides, manual-step acks, kill/skip/force directives.
- **Review and improvement:** editing process specs, runbooks, adapter configs,
  evaluators, gate thresholds.
- **Experimentation:** launching runs and experiments; comparing results; promoting
  winning metaparameter sets.

The interaction plane is **actor-agnostic**: every command works identically whether a
human, an outer coding agent, or a cron job invokes it.

### 3.3 Cross-plane interaction

Planes are a decoupling frame, not isolation barriers.
Real flows cross planes all the time, and that is the point: each plane does its one
thing and hands off cleanly.

Representative flows:

- **Control → Execution:** dispatch (run this step with these inputs, this run context,
  these metaparameters).
- **Execution → Control:** trace signals back into state transitions; adapter exit codes
  inform retry classification.
- **Execution → Observation:** artifacts and trace land in the run record.
- **Control → Observation:** state transitions, process events, run records, tool-usage
  records, cost reports.
- **Evaluation → Observation:** measurements and verdicts join the run record.
- **Interaction → Control:** commands.
- **Interaction → Evaluation:** overrides supply operator verdicts directly.
- **Execution → Control (meta-circular):** an execution-plane step can produce a new
  process spec as its artifact; on the next run, that spec reshapes how the control
  plane dispatches.

Things worth naming as cross-plane:

- **Fallbacks and retries** live primarily on the control plane but are *informed* by
  execution (error class, heartbeat) and evaluation (verdict, threshold).
- **Cost management, error surfacing, usage reporting** are observation concerns; their
  data spans all three producer planes.
- **Process improvement (the Type C loop)** is a full cross-plane loop: interaction
  initiates, control dispatches a retrospective process on the execution plane,
  evaluation judges prior foresight runs, observation renders the comparison, and the
  execution-plane output becomes a new process spec that reshapes future control
  decisions.

### 3.4 Abstraction profiles

Three levels of generality, *orthogonal* to the planes:

- **Core model:** the durable ontology: process, step, artifact, plan, status, attempt,
  result, event. Kept small.
- **Execution profile:** execution semantics: modes
  (`manual | agent | code | composite`), `for_each`, task execution, parameter binding,
  reuse policy, publication semantics, adapter contract.
- **Application profile:** domain conventions registered as plugins.
  Examples: domain prediction workflows, retrospectives, research research,
  learn/proposal/apply loops, autoresearch-style mutation/evaluation loops.

Application examples never leak back into the core schema.

## 4. Concepts and Vocabulary

### 4.1 Structure and ownership

- **Process:** a defined workflow consisting of steps, supporting documents, and
  configuration. Authored as a `<node>.process.md` file with YAML frontmatter and
  markdown body. A process can be arbitrarily complex, mixing code and agent behavior
  across steps, and may reference any number of other files including other
  `*.process.md` files.

- **Step:** a unit of work within a process.
  Each step has a *mode* that determines how it executes.

- **Harness:** the Metaproc framework as a whole: spec parsing, plan resolution,
  variable expansion, validation, state management, and orchestration.
  The harness owns everything outside a step’s internal reasoning.

- **Orchestrator:** the runtime component of the harness that executes a process.
  Concretely, the `run-process` command: it walks the step DAG in topological order,
  dispatches each step according to its mode, records state, and handles resume.
  “Harness” describes ownership and contracts; “orchestrator” describes runtime
  execution.

- **Agent:** any third-party agentic runtime that executes inside a step (Claude Code,
  Pi, Codex, Gemini CLI, etc.). Agents own reasoning inside a step; the harness owns
  everything outside.

- **Adapter:** the interface that describes how to invoke a specific agent CLI and its
  parameters. Each adapter type (e.g., `claude-code-cli`, `pi-cli`, `gemini-cli`) maps
  framework config (model, tools, timeout) to concrete CLI flags.
  The process spec’s `adapters` map defines named adapter configurations; the
  `--variant` flag selects which one to use at invocation time.

- **Variant:** a variant describes one set of metaparameters for a run; currently, in
  the process spec’s `adapters` map, variant is the name of the adapter the
  `{{run.variant}}` template variable resolves to the adapter name, so runs with
  different models or coding agents have different output filenames.

### 4.2 Items, fan-out, and map

Steps operate on specific values, or items.

- **Item:** a generic data point passed between steps.
  Can be a whole document, a structured record, or a scalar value, in files of some
  format. A scalar artifact contains one item; a list-typed artifact contains many.
  Items live at or inside artifact boundaries.
  Use “item” by default; context (the `inputs:` block, `outputs:` block, or `for_each`
  binding) tells you whether the item is inbound, outbound, or being iterated over.
- **Map item:** a structured item, typically a record (YAML map) with named fields, that
  drives one iteration of a fan-out step.
  Structurally a map; functionally the element being mapped over.
  The type `list<map_item>` is the binding type for an items file.
- **Items file:** a list-typed dep whose contents are `list<map_item>`, driving a
  fan-out step (e.g., `items.md`, `events.md`). The items file is the *candidate
  source*; per-item completion state lives separately.
  Analysis-domain code uses *roster* as a synonym; the framework does not.
- **Map:** a step applied to each element of a set of items.
- **Fan-out:** the operation of running a map using parallel workers across input items,
  dispatched by the harness.
- **Task:** the runtime execution unit produced when the harness applies one step to one
  item. Scalar steps have one task for the step.
  `task` is a runtime term used by state and log paths; it is not an authored process
  object or a synonym for item.

Code keeps `fan-out`; design conversations may use either depending on which framing is
more useful in context.

### 4.3 Core data model

- **Plan:** the resolved execution plan for a process, computed by `build_plan()`. A
  first-class runtime artifact.
- **Artifact:** a declared output of a step.
  File-based by default.
  Artifacts define the harness/agent boundary.
- **Status:** the harness-owned state record for a task.
  States: `pending | running | completed | failed | cached`. May be extended by
  `partial` and `force_advanced` once the verdict primitive generalizes.
- **Attempt:** the record of what was actually launched for a given task: params,
  inputs, outputs, runtime details.
- **Result:** the final validated outcome for a task.
- **Event:** a structured runtime log entry.

### 4.4 Run context and parameters

- **Run context:** the full envelope of parameters and runtime knobs that frame *this
  particular run* of a step.
  Injected by the harness or supplied at invocation; the step does not invent it.

Run context contains **parameters**, distinguished by *how the value is used*:

- **Parameter:** a value used as a variable *inside* the step’s content, substituted
  into prompts, paths, or runbooks via `{{...}}`, or read by a code handler as input.
  The agent or handler sees parameters as data.
  Examples: `run_id`, `item`, `event_id`, dates, resolved dep paths.
- **Metaparameter:** a value that determines *how* Metaproc runs the step: which
  adapter, which model, with what timeout and retry policy, whether and how to ensemble,
  which evaluators to apply, what gate thresholds.
  The harness consumes metaparameters to decide what to dispatch and how to interpret
  the result. Examples: model, temperature, prompt version, ensemble size, consensus
  algorithm, evaluator versions, gate thresholds.

Parameters answer “what data does the agent see?”. Metaparameters answer “how does the
harness dispatch the agent and judge the result?”. Some values cross over (a
metaparameter like `model` may also appear as a templated `{{model}}` inside a prompt);
the classification is by *primary use*.

**Reuse semantics.** Most parameters are inert (`run_id`, `item`) and excluded from the
reuse key. Most metaparameters are semantic (model, prompt version, ensemble size) and
included. Two runs with identical inputs and identical metaparameters are the same
experiment.

### 4.5 Execution concepts

- **Run pool:** the adaptive process manager for concurrent agent invocations within a
  fan-out step. Manages concurrency, memory pressure, health monitoring, and subprocess
  lifecycle. See [arch-runpool.md](../../../docs/arch/arch-runpool.md) for full design.
- **Step mode:** one of `manual | agent | code | composite`. `manual`: human acts,
  harness validates after the operator acknowledges completion.
  `agent`: coding-agent subprocess via an adapter.
  `code`: deterministic handler or shell command.
  `composite`: delegates to a child `*.process.md`. See §1.3 for the practical execution
  surface and the work-semantics framing.
- **Backend:** the execution environment for agent subprocesses.
  `local` runs subprocesses on the current machine; `gcp-worker` dispatches items across
  cloud VMs. The backend determines *where* the run pool operates, not *what* it runs.

### 4.6 Run record

What a step run produces *beyond* the artifact.
Persistent, keyed to the run, queryable after the run completes.
Four sub-kinds, each with a different owner and lifecycle:

- **Trace:** operational telemetry the *runtime* observes: tool-call sequence, timings,
  costs, retries, error classes, resource usage, harness events.
  Written by the harness, not by the step.
  Cannot be reconstructed after the fact; must be captured live.
- **Evaluations:** named, versioned metric computations applied to a run.
  An evaluation is `(metric_spec, evaluator_version)` applied to `(artifacts ∪ trace)`,
  producing a typed value.
  Re-runnable later under a new evaluator version; runs accumulate evaluations over
  time.
- **Annotations:** flexible structured key/values attached by the step, by an evaluator,
  or by a human. Typed namespaces for well-known fields; a free-form `extras.*` namespace
  for one-off researcher notes.
- **Verdict:** gate decision (`pass | fail | force_advanced`), plus the evaluations it
  was computed from and (if forced) an actor and reason.
  Separate from the evaluations themselves so re-evaluation does not imply re-gating.

**Produced-by vs produced-about.**

- *Produced by the step:* artifacts, step-emitted annotations, any metrics the step
  itself computes. Live-only; cannot be reconstructed after the run.
- *Produced about the step:* trace (runtime), evaluations (evaluators, typically run
  after step completion), verdicts (gate engine).
  Can be re-computed if the artifacts are durably addressable.

This asymmetry drives the storage model: the run record is filesystem-resident,
extending existing `.state/` and `.logs/` artifacts rather than introducing a new
storage layer.

### 4.7 Per-step quality

The grammar reads: *evaluators produce measurements; measurements aggregate into
metrics; gates turn measurements into verdicts.*

- **Evaluator:** a function that judges a run.
  Named, versioned. Can be a prompt judge, deterministic code, a sub-process, or a
  composite.
- **Measurement:** a single typed value an evaluator produces for a single run (e.g.,
  `cost.tokens_in = 14203`, `quality.rubric_v3 = 0.82`). Has a unit and a direction
  (higher-better or lower-better).
- **Score:** a *normalized* measurement (0–1, pass/fail, letter grade).
  Every score is a measurement; not every measurement is a score.
  The word “score” signals “comparable across runs.”
- **Metric:** an aggregation of measurements across a *set* of runs: pass rate across a
  comparison set, p95 latency across a cohort, mean rubric score across a fan-out.
  A single run does not have a “metric”; it has measurements.
- **Verdict:** gate outcome (`pass | fail | force_advanced`), with provenance back to
  the measurements that drove it.
- **Eval:** the umbrella noun/verb for the activity ("run the rubric eval"), not a name
  for the data. The eval *produces* measurements.

The word “result” is too generic to mean a single run’s output bundle; the word for the
full bundle is **run record**.

**Framework evals vs domain evals.**

- **Framework evals:** always-on, uniform across every step.
  Populate the run record automatically with structural completeness (did it produce
  declared artifacts, pass schema, terminate cleanly) and operational telemetry
  (tool-call counts, cost, retries, wallclock, tool-budget adherence).
  Harness-owned. Gates on these catch runaway agents and malformed outputs without
  per-step opt-in.
- **Domain evals:** per-step, author-defined.
  Includes anything rubric-based, ground-truth-comparing, or with domain-specific
  quality dimensions (research’s *completeness, correctness, consistency, usefulness*).

The Type A loop (improve the step) varies metaparameters across a comparison set with
framework evals held constant, so comparisons are fair.
The Type B loop (improve the measurement) iterates on domain evals with the step frozen.

## 5. Optimization Loops and Self-Improvement

Processes can read, evaluate, and rewrite other processes, including themselves.

### 5.1 Meta-circularity

A process definition is itself a file.
A Metaproc process can read other process definitions, validate them, evaluate them
against execution results, and produce revised versions.
**Self-improvement is just process composition.**

Process definitions can be:

- **Reflected on:** an agent or human reads the definition and reasons about structure,
  dependencies, and gaps.
- **Validated:** the harness checks artifact contracts, detects missing steps, and
  dry-runs the process before execution.
- **Improved:** a meta-improvement process reads the definition, compares it against
  execution results, and produces a revised version.

Meta-circularity requires that structure be *declared, not buried in imperative code*.
The process can only improve itself if it can read itself.

The Type C optimization loop (§5.3.3) is meta-circularity in action: a retrospective
process whose output reshapes the process it analyzes.

### 5.2 Vantage

Self-improvement requires running a process, then analyzing how good its results were,
and going back to improve the process.
For example, a process that makes a prediction executes before an event happens, then a
process afterwards can analyze the quality of that prediction.

This pattern is common so we use the term **vantage** to describe which of these two
views a process is taking:

- A process or step that uses **foresight** produces an artifact that will later be
  compared against an outcome not yet known.
  Foresight includes predictions, forecasts, and forward-looking theses.
  Foresight steps *must not* have access to the outcome; leaking outcome data into a
  foresight step invalidates its evaluation.
- A process or step that uses **hindsight** has full information and reasons about
  events whose outcomes are already known.
  Back-tests, ground-truth labeling, post-mortem cruxes.
- A process or step is **retrospective** if it uses hindsight to improve a foresight
  process. A retrospective process is a process where *the inputs and outputs are
  processes themselves.*

A process can include both foresight and hindsight steps or sub-processes.
Retrospective analysis is the concrete mechanism by which the Type A and Type C loops
become automatable rather than human-only.

Vantage is a domain concept (the engine does not need to know about it), but it shapes
how evals are constructed and which steps can legitimately drive process-improvement
loops.

### 5.3 The three loops

Three concrete shapes of iterative improvement.
The choice depends on which dimension is the current bottleneck:

- **Type A → metaparameter variation** with framework evals held constant
- **Type B → durable artifacts** (§4.6) and **versioned evaluations** (§4.7)
- **Type C → retrospective processes** (§5.2) producing new process specs

The first-class primitives that mechanize Type A and Type C (sweep, ensemble,
experiment) are deferred work; see
[metaproc-design-rev3-proposals.md](../../../docs/metaproc-design-rev3-proposals.md) P7
for the vocabulary and a grounding example that orchestrates the pattern manually today.

#### 5.3.1 Type A: improve the step

Hold inputs, parameters, and measurement constant.
Iterate on the step itself: prompt, code, model.
Compare measurements across the comparison set to identify what works.

This is the narrow auto-research loop.
It works only when the loop is reliable enough to run unattended (~5-minute iterations,
~100 attempts overnight); even at 90% failure, the remaining successes net meaningful
progress.

The Type A loop operates on a set of runs that share upstream and run context, varying
on declared metaparameter axes.
Framework evals (§4.7) are held constant across the comparison so the only thing that
differs between runs is what’s on the metaparameter axis.

#### 5.3.2 Type B: improve the measurement

Hold the step constant.
Iterate on the **evaluators** (§4.7). Use this when the output may be good but the eval
cannot yet tell.

The Type B loop requires that **artifacts be durably addressable** (§4.6) and
**evaluations be versioned and re-runnable** (§4.7); otherwise frozen runs can’t be
re-scored against new evaluators.
The whole point of the Type B loop is to go back to runs that already happened and apply
better measurements; that’s only possible if the artifact storage and the evaluation
history both support it.

#### 5.3.3 Type C: improve the workflow shape

Split, merge, or reorder steps when A and B plateau.
The most expensive loop, and the one most likely to require structural re-thinking.

A **retrospective process** (§5.2) is the natural shape of the automated Type C loop: it
consumes prior foresight runs and their hindsight ground truth, and emits candidate
changes to the process spec itself.
Because process definitions are first-class artifacts (§5.1 meta-circularity), the
output of a retrospective *is* the input to the next iteration of the foresight process:
the Type C loop is process composition.

### 5.4 Preconditions for any loop

A precondition for agent-driven optimization in any loop is a **bounded, enumerable
lever space** per step (~10–20 approaches, not 100). Defining that menu is a human
prerequisite, not an agent deliverable.
Without it, an agent driving the Type A loop searches an unbounded space and makes no
measurable progress.

A second precondition is **loop reliability**: the orchestrator must complete hundreds
of unattended iterations before any optimization loop is empirically tractable.
See principle 4 in §6.1 below.

## 6. Principles

Each principle below resolves a design question that violating it would reopen.

### 6.1 Boundary and artifact discipline

Files are the universal contract.
What’s inside a step is anyone’s business; what crosses a boundary is everyone’s.

1. **Boundary-first.** Files are the step boundary; schemas are declared at the
   boundary; everything inside a step can be arbitrarily messy.
   Add structure when repeated patterns earn it (see §2 for the adoption path).

2. **File-based integration.** Files are universal across tools, visible to humans and
   agents, resumable, versionable, natural for coding agents.
   Artifacts are typed; completion is based on validation, not just file presence;
   status and attempt state are emitted as structured runtime artifacts.

3. **Declarative outer structure, freeform inner reasoning.** The process spec declares:

   - what steps exist
   - which parameters they require
   - what they read
   - what they write
   - what depends on what
   - how fan-out works
   - what runtime profile applies

   The runbook describes:

   - how the agent thinks
   - how it researches
   - how it inspects inputs
   - how it decides among alternatives inside the step

4. **Loop reliability is separate from step flexibility.** Inside a step, anything goes
   (full coding agent, freeform tools).
   Around the steps, the orchestrator must reliably complete 100–1,000 unattended
   iterations so Type A/B/C optimization is empirically possible.

### 6.2 Harness reliability and resume

The framework is small and deterministic on purpose.
Reliability comes from absence of moving parts.

5. **Lightweight substrate.** State is filesystem-resident.
   Run records, evaluations, and annotations extend existing `.state/` and `.logs/`
   artifacts; richer query surfaces are *derived* from filesystem state, not separate
   stores. Specifically:

   - **No scheduler daemon:** orchestration is a CLI command, not a long-running
     service.
   - **No database:** all state is filesystem-based (YAML, JSONL, NFS).
   - **No server:** the `serve` command is a read-only browser, not a control plane.
   - **No branching DSL:** step execution order is determined by a static dependency
     graph.
   - **No embedded expression language:** template variables use simple `{{VAR}}`
     substitution.
   - **No multi-agent protocol model:** agents communicate via file artifacts only.
   - **No large ontology of artifact types:** the core model remains small.

   The cloud execution layer adds GCP Batch and Filestore NFS, but these are used as
   infrastructure for running the same CLI commands in containers; the framework does
   not depend on cloud APIs for correctness.
   A local `run-process` invocation produces the same results as a cloud one.

6. **Harness owns orchestration.** The harness owns:

   - step selection
   - dependency ordering
   - fan-out discovery
   - status transitions
   - launch policy
   - validation
   - publication of completion state
   - resumability
   - retry decisions
   - resource monitoring
   - log lifecycle (compaction, runaway detection)

   The agent owns:

   - reasoning inside a step
   - tool usage inside a step
   - writing the step’s declared outputs

   The harness never depends on conversational state to know whether a step is complete.

7. **Idempotent resume must be easy.** Resuming a partial run must be a normal operating
   mode, not a special recovery path.
   Specifically:

   - the unit of resumability is the item-step run
   - rerunning a process must safely skip completed work
   - failed work must be retryable without manual cleanup in the normal case
   - stale `running` work must be reclaimable by the harness
   - completion must be determined by harness-owned atomic state plus output validation
   - partial outputs alone must not count as success

8. **Shared mutable state belongs to the harness.** Many agents must not write the same
   shared items file. Specifically:

   - agents write only their own outputs
   - the harness writes shared status state
   - the items file (e.g., `items.md`) is a planning and summary surface, not a lock
     manager
   - source artifacts used for fan-out must remain readable and trustworthy

### 6.3 Run context, reuse, and caching

The split between what’s templated and what’s dispatched is the rule that makes reuse
principled and structured comparison coherent.

9. **Parameters are templated; metaparameters are dispatched.** Parameters live inside
   the step’s content (substituted via `{{...}}` or read by code handlers);
   metaparameters live outside (consumed by the harness to dispatch the step and judge
   the result). Inert parameters (e.g., `run_id`) stay out of the reuse key; semantic
   metaparameters (e.g., `model`) go in.
10. **Artifacts are immutable and durably addressable.** The Type B loop (improve the
    measurement) is only possible if evaluations can be re-run against frozen artifacts.
    Ephemeral artifact storage kills the Type B loop silently.

### 6.4 Evaluation and gating

Quality is measured uniformly at the framework level and richly at the domain level.
Gates are predicates over measurements; overrides are signed waivers.

11. **Framework evals are always-on and uniform.** Every run emits the same framework
    signals: structural completeness and operational telemetry.
    Domain evals are opt-in and author-defined per step.
12. **Gates are separate from measurements.** Measurements are continuous and
    evaluator-owned; gates are threshold predicates over measurements and engine-owned.
    Re-evaluate without re-gating; re-gate without re-evaluating.
13. **Evaluations are versioned and non-destructive.** A run record accumulates
    evaluations over time; a new evaluator version does not overwrite prior
    measurements. Longitudinal comparison depends on this.
14. **Overrides are first-class and audited.** A gate override is a signed waiver, not a
    flag flip: required `actor` and `reason`, surfaced in status, recorded in the run
    record. Force-advanced runs are filterable out of baselines.
15. **Typed core + namespaced free-form extras.** Operational and completeness signals
    live in a typed registry (name, unit, direction, schema); everything else is a
    free-form `extras.*` namespace.
    This is the resolution of the loop-reliability vs step-flexibility tension; do not
    soften it later under pressure.

### 6.5 Planes and actor-agnostic interaction

Producers and consumers are decoupled.
The interaction plane treats every actor identically.

16. **Producers do not know about consumers.** Control, execution, and evaluation planes
    write authoritative state; observation surfaces (monitoring + visualization) render
    it. Producer planes do not reach into the renderers.
    This is what lets dashboards and browsers evolve without destabilizing the runtime.
17. **The interaction plane is actor-agnostic.** Every CLI command, override, spec edit,
    and experiment setup works identically whether invoked by a human, an outer coding
    agent, or a cron job.
    This is what makes agent-driven experimentation work on the same surface humans use.
18. **Four-mode uniformity.** Framework evals, run records, gates, and overrides apply
    uniformly across `manual | agent | code | composite` step modes.
    A manual step has trace, evaluations, and a verdict, just as an agent step does.

### 6.6 Vantage and meta-circularity

Self-improvement is process composition, and its honesty depends on respecting the
temporal stance of each step.

19. **Vantage is labeled per step.** Foresight, hindsight, and retrospective vantages
    differ in information access, eval timing, and role in the optimization loop.
    Labeling the vantage makes leakage bugs detectable, makes ground-truth-producing
    processes legible as such, and makes retrospective (process-improving) processes
    first-class rather than ad-hoc.
20. **Meta-circularity.** A declarative process definition is a first-class readable
    artifact. A meta-improvement process can read a definition, compare it against
    execution results, and produce a revised version.
    This works only if structure is *declared, not buried in imperative code*.

### 6.7 Evolution discipline

Abstractions are earned by recurrence, not anticipated by design.
Every primitive added must justify its place along the three axes.

21. **Codification follows experiment.** Don’t pre-build abstractions; let a pattern
    recur in at least two real iterations before promoting it to typed infrastructure.

22. **Structural change discipline.** When live use reveals a failure, classify it
    before adding schema:

    1. `context` problem
    2. `question` or control problem
    3. `structural` problem

    Only repeated structural failures graduate into new runtime features.

23. **Three-axes correspondence.** Every primitive added must advance at least one of
    the three axes (automation, exactness, structure) without freezing the others.
    Overrides + force-advanced verdicts are the *gradual-automation* affordance.
    Evaluators codifying recurring tolerances are the *exactness* affordance.
    Schemas on the run record (typed core + extras) are the *structure* affordance.

### 6.8 Strategic operating principles

How to spend tokens, sequence work, and invest depth.

24. **Pre-computing is never obsolete.** Search pre-cached pages; LLMs pre-cached
    language; the next stage pre-caches verified insights, theses, and structured
    knowledge. If useful work can be pre-cached, pre-cache it.
    Not doing work is cheaper than doing it with a better model.
25. **Code outscales caching.** Caching is near-free at lookup time; good code and
    infrastructure compound across every caller forever.
    This justifies disproportionate scarce-token spend on critical code (e.g., multiple
    alternating model passes on a single critical file): even one bug caught pays back
    indefinitely.
26. **Token allocation is nonlinear.** Cost mistakes are orders of magnitude, not
    percentages. Scarce top-tier tokens belong on critical code, schemas, and
    high-leverage data; cheap tokens handle bulk work.
    A workflow that seems affordable at small scale can become impossible if caching and
    token routing are wrong.
27. **Spend top-tier tokens on schema design at the boundaries before spending them on
    what’s inside the step.** The shape of the output catches more problems than
    refining the implementation; improvements inside the box back-propagate naturally
    from a well-shaped output.
28. **Go deep before broad.** Build one narrow workflow or knowledge vertical to high
    quality, then translate sideways.
    LLMs are themselves the lateral-translation engine: once a vertical is deep and
    codified, mapping it to adjacent domains is nearly free, which is *why* depth-first
    compounds harder now than it used to.
29. **Optimize one step at a time.** Otherwise downstream failures obscure whether the
    real defect lives upstream, and signal from each stage dilutes as it propagates.

### 6.9 Engine boundary: process concerns stay in processes

When a capability can be expressed as “a step runs a shell command” (git clone, git
commit, gh pr create, rsync, gsutil cp), it belongs in the process spec, not in the
metaproc engine. The engine stays filesystem- and tool-agnostic: it runs steps, tracks
state, manages concurrency.
It does not grow knowledge of git, GCS, GitHub, or other tool-specific concerns.

If setup must happen *before* any step runs (seeding a worker with a git checkout,
mounting Filestore), put it in the dispatch / deployment layer (Batch job template,
`metaproc gcp run` pre-hook), not in the engine.

Engine-level abstractions for process-shape concerns leak domain logic into metaproc and
create N code paths where one process spec would do.

## 7. Positioning and Comparisons

The closest analogues each contribute one intuition:

- **Make:** the file-dependency intuition.
  A step’s output is determined by its declared inputs, and downstream work is
  recomputed only when those inputs change.
  Metaproc is Makefile-like at the boundary, with typed artifacts and validation-based
  completion.
- **Docker:** the cache-stability intuition.
  Once a lower stage is good enough, stop paying to rerun it unless deliberately
  invalidated. Metaproc borrows this intuition for cacheable step outputs, applied to
  workflow stages rather than container images.
  Docker alone is too frozen (zero-input stages) for evolving workflows.
- **Airflow:** the DAG intuition: multi-step execution with measurable artifacts.
  Metaproc has the DAG, but adds typed file interfaces and flexible agent steps that
  Airflow does not have.
- **Agent SDKs:** in-step ergonomics.
  The vendor SDKs (Claude Agent SDK, OpenAI Agents SDK, Vercel AI SDK) drive a single
  agent loop inside one application process.
  Metaproc sits a layer above them: it drives a multi-step process that dispatches one
  or more agent invocations as subprocess steps, with file artifacts as the boundary.
  A `mode: agent` step can wrap any of these SDKs inside a single subprocess when one
  step needs that SDK’s in-process ergonomics.
  The file-artifact contract at the step boundary is preserved either way.

### Detailed agent-SDK comparison

The most popular options today:

- **Claude Agent SDK:** Deepest built-in tool and hook surface; automatic compaction;
  accepts subscription auth.
  Locked to Claude. Drives one agent session, not a process.

- **OpenAI Agents SDK:** Cleanest multi-agent primitive (explicit handoffs), built-in
  guardrails, unique hosted tools (code interpreter, file search).
  Non-OpenAI models only via LiteLLM; hosted tools break on other backends; API-key
  billing only.

- **Vercel AI SDK:** True provider neutrality and best-in-class streaming UI hooks.
  No built-in tools, thinner agent loop, no compaction or guardrails, TypeScript only.
  Targets apps, not batch processes.

These are not all solving the same problem:

- One agent, one interactive task → Claude Agent SDK or OpenAI Agents SDK.
- One agent, streaming chat UI, provider-swappable → Vercel AI SDK.
- Many agents, many steps, heterogeneous models, composition, self-improvement →
  Metaproc.

|  | Claude Agent SDK | OpenAI Agents SDK | Vercel AI SDK | Metaproc |
| --- | --- | --- | --- | --- |
| Integration | In-process lib (Py/TS) | In-process lib (Py) | In-process lib (TS) | Out-of-process CLI subprocess |
| Contract | In-memory message stream | In-memory message stream | In-memory message stream | File artifacts + JSONL events |
| Providers | Claude only | OpenAI native; LiteLLM/AnyLLM shim for others | 24+ first-class (OpenAI, Anthropic, Google, Bedrock, Ollama, …) | Claude Code, Gemini CLI, Codex CLI, Pi (→ Anthropic/OpenAI/Google/Vertex MaaS/Azure/Bedrock + any OpenAI-compatible endpoint); any other CLI adapter is straightforward to add |
| Built-in tools | Read/Edit/Write/Bash/Grep/Glob/WebSearch/Agent/Skill | Hosted (WebSearch, FileSearch, CodeInterpreter, MCP) + Shell/ApplyPatch/Computer | None (exposes provider-native tools only) | Whatever each CLI ships |
| Loop control | `maxTurns`, `maxBudgetUsd`, effort, permission-mode | `max_turns`, handoffs; no `$` cap | `stopWhen`, `prepareStep` | Per-step budget/timeout; DAG bounds the run |
| Multi-agent | Subagents via `Agent` tool | First-class handoffs with input filters | Nested `generateText` | Composite step → child `*.process.md` |
| Context mgmt | Automatic compaction, sessions | Pluggable session stores (SQLite/Redis/SQL/Dapr) | Caller-managed messages | No conversational state across steps; artifacts carry forward |
| Hooks / guardrails | `PreToolUse`, `PostToolUse`, `Stop`, `PreCompact` | Input/output/tool guardrails with tripwires | Lifecycle callbacks + OpenTelemetry | Post-hoc event parsing, QA plugins |
| Auth / billing | API key or Claude Max/Pro subscription | OpenAI API key only | Per-provider API keys | API key, interactive login, or Max/Pro OAuth via Secret Manager |
| Sweet spot | One Claude-style agent in one app | Multi-agent orchestration on OpenAI | Streaming chat UIs in React/Next.js | Long-running, multi-step, multi-model batch processes |

## References

- [arch-metaproc-core.md](../../../docs/arch/arch-metaproc-core.md): implementation
  reference (spec format, runtime artifacts, CLI, adapters, cloud execution).
- [research-2026-04-24-metaproc-step-evaluation-and-optimization.md](../../../docs/metaproc-design-rev3-proposals.md):
  detailed terminology pass with explicit `Maps onto:` annotations.
- [april-24-metaproc-improvement-loops-sharpened-notes.md](../../../docs/metaproc-design-rev3-proposals.md):
  source notes for Type A/B/C optimization loops and the strategic operating principles.
- [std-doc-guidelines.md](../../../AGENTS.md): documentation conventions used here.

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
