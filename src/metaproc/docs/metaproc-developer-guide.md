---
title: Metaproc Developer Guide
description: How to use and extend the framework — authoring processes, adding steps and adapters, and testing changes.
---
# Metaproc Developer Guide

Related docs: [concepts](metaproc-concepts-and-principles.md) (first principles) ·
[operator reference](metaproc-operator-reference.md) (runtime CLI).

## Purpose

For engineers extending metaproc or building a workflow on top of it.
Read this before adding a CLI command, a process-spec feature, or — especially — before
writing a script that wraps metaproc.
It is generic to metaproc, not specific to any one workflow.

## The Core Principle: Metaproc Is the Right Wrapper

Metaproc is the wrapper around dispatch, run pools, fingerprints, traces, rollups, and
gates.
**Workflows call metaproc directly.** When metaproc is hard to use that way — when
a workflow seems to need a Python or shell layer to compose, walk, format, or aggregate
metaproc invocations — that is the signal to improve metaproc’s abstractions, not to
build the layer. Every wrapper that grows alongside metaproc rebuilds capabilities the
framework already owns, fragments operator vocabulary, and traps the workflow inside a
per-project orchestrator.
The cost of growing metaproc is paid once; the cost of carrying a wrapper compounds
forever.

## Principles

- **Process specs over orchestrators.** A multi-step flow is a `*.process.md`, not a
  Python driver that calls `metaproc run-process` in a loop.
- **Extend metaproc for run-state views.** A new way to see run state is a `metaproc`
  subcommand, not a script that hand-parses run-dir files.
- **Small focused helpers are fine.** Single-purpose, non-orchestration helpers
  (calendar pull, ticker classification, template rendering) are healthy.
  A multi-step helper that calls metaproc in sequence is a process spec in disguise.

## Antipatterns

1. **Python orchestrator that compiles and walks a DAG of `metaproc run-process` calls**
   — that DAG is a process spec in disguise; author it as a process file.
2. **Shell script that hand-parses run-dir state** (lease files, `process-events.jsonl`,
   `runpool-events.jsonl`) — use `metaproc pulse` and the status commands.
3. **Per-project Python “setup” helper with multiple subcommands wrapping metaproc** —
   write the process spec or the missing metaproc subcommand instead (the EIA kickoff
   skill deliberately ships no `setup_batch.py`).
4. **Dual paths during a refactor** — switch every call site; do not keep the old
   wrapper alongside the native path beyond the parity window.
5. **Conditional process steps gated by a top-level flag** — express multiplicity and
   gating with the framework’s primitives, not an `if` inside a step.

## When Tempted to Work Around Metaproc

The temptation to reach for a wrapper is itself the signal that metaproc needs to grow.
File a bead against metaproc, simplify a CLI shape, or surface the missing primitive —
and keep the workflow calling metaproc directly.
The overarching goal is that both the framework and the workflows on top of it stay
flexible yet minimally complex.
See [`metaproc-concepts-and-principles.md`](metaproc-concepts-and-principles.md) for the
design ethos.

## Adapter Contract: `classify_failure` Is Mandatory

**Every adapter MUST implement `classify_failure`.** The base-class default returning
`unknown` is a footgun: it causes deterministic failures (auth, missing binary, schema
mismatch) to be retried as generic crashes, wasting time and hiding the real cause.
A coding-agent CLI failing to run is a failure exactly like Python failing to start —
not a transient condition to retry.

The contract:

| Failure class | Adapter MUST return | Why |
| --- | --- | --- |
| HTTP 401 / 403, `Expected OAuth2`, `invalid_grant`, `unauthorized`, expired or missing credential | `AuthFailureClassification(status="expired", severity=FailureSeverity.ABORT, reason="<adapter>-<specific>")` | Deterministic; retrying with the same bad credential will keep failing. |
| Binary not on PATH, version too old, mandatory CLI flag rejected | `severity=FailureSeverity.ABORT, reason="<adapter>-binary-or-version"` | The CLI can’t run. |
| Same validator rejection across N attempts, or `known_bugs.py` signature match | `severity=FailureSeverity.ABORT, known_bug_signature=<name>` | Software bug; retrying won’t fix it. |
| Required env var missing AND strictness flag on (e.g. `EIA_REQUIRE_ALL_WEB_BUNDLE_KEYS=1`) | `severity=FailureSeverity.ABORT, reason="<adapter>-missing-env-<KEY>"` | Same deterministic class. |
| Terminal quota — “monthly usage limit reached” with no reset, billing failure, account suspended | `severity=FailureSeverity.ABORT, reason="<adapter>-terminal-quota"` | Operator must intervene. |
| HTTP 429, `rate_limited`, `Overloaded`, Anthropic ratelimit headers | `severity=FailureSeverity.RETRY_AFTER_WAIT` (parse `cooling_until_ts` from reset headers/text when available) | Transient; will recover after the named window. |
| HTTP 5xx, network errors, connection reset, stream-idle timeout (host suspend) | `severity=FailureSeverity.RETRY_NOW` | Transient; immediate retry usually works. |
| Nothing matched | `AuthFailureClassification(status="unknown", reason="generic")` — the generic retry classifier wins | Fail open (less destructive). |

Reference implementations: `src/metaproc/adapters/claude_code.py` and
`src/metaproc/adapters/codex.py`. When adding a new adapter or filling a gap, mirror the
structure: check terminal signals first, then `known_bugs.py`, then soft rate-limit
family, then return `unknown`.

**Today’s gaps** (filed in
[`plan-2026-05-25-metaproc-autopilot-and-step-budgets.md`](../../../TODO.md) § Phase 0):

- `src/metaproc/adapters/gemini.py` — no `classify_failure`. 401 from Vertex
  (`Expected OAuth2 access token` because `GOOGLE_API_KEY` conflicts with
  `GOOGLE_GENAI_USE_VERTEXAI=true`) falls through to generic retry.
  The 2026-05-26 Tue AMC batch burned 88 retries on this.
- `src/metaproc/adapters/pi_cli.py` — same gap.
  `pi` routed through `--provider google-vertex` has the same auth surface and the same
  risk; classifier must cover the pi-side 401 / `Unauthenticated` patterns as well as
  Vertex MaaS (GLM) auth failures.

Phase 0 of the autopilot+budgets spec elevates `classify_failure` from optional to
required (no base-class default fallback), adds the two missing implementations, and
wires the abort signal into the wrapper-log surface so operators see the actual error
text instead of an opaque exit code.

## Where New Things Go

| New need | Lands as |
| --- | --- |
| A new view of run state | a `metaproc` subcommand |
| A new step mode, fan-out, or gate semantic | a metaproc bead (framework change) |
| A new artifact category multiple workflows emit | an `artifact-catalog.md` entry |
| A new operator interaction pattern (prompts, confirmation gates) | a Claude Code skill that calls metaproc directly |

A skill is orchestration glue plus pointers, never a documentation home: its substance
must already live in a doc or a self-documenting CLI command (`metaproc help <topic>`,
`--help`), and the skill references it rather than restating it.
Skills are **self-generated**: the source baseline lives in the tool package and
`metaproc skill <name> --install` composes the `SKILL.md` to the portable
`.agents/skills/<name>/` path (cross-agent) and mirrors it to `.claude/skills/<name>/`
(gitignored, `DO NOT EDIT`); a workflow registers its skill via a `metaproc.skills`
entry point. See the **Skills and Agent Instruction Files** rules in
[`AGENTS.md`](../../../AGENTS.md).

## Suggested Vocabulary for Fan-Out Workflows

These are workflow-organizing conventions, not part of metaproc itself:

| Term | Meaning |
| --- | --- |
| **batch** | a composite parent run over a day’s work (multiple tiers) |
| **tier** | a slice of a batch — a group of items run together |
| **roster** | the operator-curated list of items (with grouping) a batch consumes |

Consumers may adopt this vocabulary without making it part of the framework contract.

## Worked Example

A client can express a daily batch as `batch.process.md` with composite references to
one or more `tier.process.md` files.
Its kickoff skill should call `metaproc run-process` directly with the selected roster
and run variables. The client owns roster selection and reporting; Metaproc owns DAG
execution, status, retries, traces, and resource controls.
The deterministic
[offline example](../../../examples/offline-smoke/offline-smoke.process.md) shows the
same process/handler boundary without domain-specific policy.

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
