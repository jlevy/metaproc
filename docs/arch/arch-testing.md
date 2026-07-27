---
title: "Architecture: Testing"
description: How to smoke-test, unit-test, and integration-test standalone Metaproc
author: metaproc team
status: Approved
---
# Architecture: Testing

**Date:** 2026-04-24 (last updated 2026-07-26) **Status:** Approved

> **Maintenance**: This is a maintained architecture doc.
> Revise via `tbd shortcut revise-architecture-doc` (which prompts you to verify content
> against current code, then add a “Future Considerations” section).
> When you make non-trivial changes, bump the **last updated** date above.
> The full arch-doc index lives in
> [development.md § Architecture docs](../development.md#architecture-docs).
> 
> Companion docs (in `metaproc/docs/`): [arch-metaproc-core](arch-metaproc-core.md),
> [arch-runpool](arch-runpool.md), [arch-cloud-execution](arch-cloud-execution.md),
> [arch-authentication](arch-authentication.md),
> [arch-claude-code-harness](arch-claude-code-harness.md),
> [arch-testing](arch-testing.md).

Testing is organized into tiers by cost and scope.
Each tier is a named process spec you run via `metaproc run-process`; the table below
maps what to reach for when.

| Tier | Scope | What it covers | Wall clock | Cost |
| --- | --- | --- | --- | --- |
| **smoke-core** | provider-agnostic | standalone lint, type, docs, policy, and unit-test gates | machine-dependent | free |
| **smoke-adapter-\<name\>** | one provider | binary + credential + model-asserted live-prompt round-trip | 10-20s | trivial (1-2 tokens) |
| **smoke-adapters-all** | every provider | Phase-1 auth-check survey + all four per-adapter smokes in parallel | ~45s | trivial (4-8 tokens total) |
| **self-test-local** | local end-to-end | deterministic three-step DAG plus output verification | seconds | free |
| **self-test-cloud-plan** | cloud plan | render a GCP Batch job without dispatch | seconds | free |

Start with `smoke-core`. If it passes, layer on the per-adapter smoke for whichever
provider(s) you are about to use, or run the `smoke-adapters-all` aggregator to probe
every adapter at once.
Only reach for the integration tiers when the smoke tiers are green.

For changes to softschema bindings, process output `schema:` declarations, generated
frontmatter reports or structure-report rendering, run
[softschema-validation.runbook.md](../runbooks/softschema-validation.runbook.md) before
any live adapter smoke.
Its default path is no-token and validates the softschema boundary map and a
negative-control failure.

## Tier 1 — `smoke-core` (provider-agnostic)

Runs the standalone repository’s committed lint and pytest gates.
No network, no credentials.

```bash
uv --config-file uv.toml run --frozen metaproc run-process \
  process/self-test/smoke-core.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=smoke-core
```

A red result always points at a code regression — no environment state affects this
tier.

## Tier 2 — per-adapter smokes (one live prompt each)

One process per registered adapter.
Each exercises three steps serially: binary presence → credential detection → live
prompt through the real provider backend.

```bash
uv run metaproc run-process process/self-test/smoke-adapter-claude.process.md
uv run metaproc run-process process/self-test/smoke-adapter-codex.process.md
uv run metaproc run-process process/self-test/smoke-adapter-gemini.process.md
uv run metaproc run-process process/self-test/smoke-adapter-pi.process.md
```

Credential requirements per adapter:

| Adapter | Credential | Setup |
| --- | --- | --- |
| `claude-code-cli` | macOS Keychain OAuth *or* `ANTHROPIC_API_KEY` | `claude auth` or Claude Desktop login |
| `codex-cli` | `OPENAI_API_KEY` *or* `~/.codex/auth.json` via `codex login` with `cli_auth_credentials_store = "file"` | See [codex setup](../runbooks/credential-setup.runbook.md) |
| `gemini-cli` | one of: (a) `GEMINI_API_KEY`, (b) `GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_API_KEY`, (c) `GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT` + ADC | See [credential-setup.md § Gemini](../runbooks/credential-setup.runbook.md#gemini-cli-gemini-cli). The smoke uses mode (c) against `$METAPROC_GCP_PROJECT` so it runs with no Gemini-specific key. |
| `pi-cli` | `~/.pi/auth.json` (plus GCP ADC for `vertex-maas`) | `pi login`; `gcloud auth application-default login` for MaaS |

The `pi-cli` smoke pins `vertex-maas` with `glm-5-maas` because that combination is free
in this project. Substitute `--variant pi-cli-<other>` and `--provider <other>` when
probing a paid path.

### Model assertion (`--assert-model`)

Each per-adapter smoke passes `--assert-model <substring>` on its live-probe step so the
harness verifies the observed model — not just the subprocess exit code.
Without this, a silent `--model` fallback (claude/gemini warn-and-default on unknown
model names) keeps the subprocess green even when the requested model was ignored.

The helper parses the CLI’s JSONL stdout for the identity event that carries `model`:

| Adapter | Identity event | Model path | Smoke expected substring |
| --- | --- | --- | --- |
| `claude-code-cli` | `system.init` | `model` | `opus` |
| `gemini-cli` | `init` | `model` | `gemini-3` |
| `pi-cli` | `message_start` (first assistant) | `message.model` | `glm-5-maas` |
| `codex-cli` | none — codex-cli’s stream doesn’t carry a model ID | n/a | informational only |

For codex, `--assert-model` emits an informational line rather than a hard assertion.
The codex model guarantee is covered by the separate negative-control smoke (invalid
`-m <model>` must exit non-zero) — see
[`smoke-adapters-negative-control.process.md`](../../process/self-test/smoke-adapters-negative-control.process.md).

### Cloud-dispatch claude credential (Phase 2b)

The per-adapter smoke for Claude explicitly unsets `METAPROC_GCP_SECRET_CLAUDE_CREDS` so
the local smoke stays green when the cloud cred is stale — the local and cloud
credential paths are independent concerns.

To explicitly probe the cloud-dispatch credential stored in GCP Secret Manager, run
`auth-check` directly with the ref:

```bash
uv run metaproc auth-check --live --variant claude-code-cli \
  --claude-secret-ref projects/PROJECT/secrets/NAME/versions/latest
```

Or run `auth-check` with no `--variant` and `METAPROC_GCP_SECRET_CLAUDE_CREDS` set in
the shell; Phase 2b auto-triggers for the no-variant and `--variant claude-code-cli`
combinations.

## Tier 2b — `smoke-adapters-all` (every live adapter in one run)

Aggregator that runs the Phase-1 `auth-check` dry survey plus the four per-adapter
smokes in parallel via `mode: composite`. Single green signal confirms every registered
adapter can dispatch laptop-local today, with model assertion closing the
silent-fallback loop.

```bash
uv run metaproc run-process process/self-test/smoke-adapters-all.process.md
```

Wall clock is bounded by the slowest adapter smoke (typically ~15 s) plus composite
overhead — expect ~45 s end-to-end when every credential is present.
Each composite child runs under its own run subdirectory, so a red child’s logs land
there and a re-run in isolation (`smoke-adapter-<name>.process.md`) gives the tightest
diagnostic.

The aggregator intentionally skips the Phase 2b Secret Manager probe (same reasoning as
the per-adapter claude smoke): a stale cloud credential should not red a laptop-local
aggregator.

## Tier 3 — local integration (`self-test-local`)

Runs the deterministic offline example as a nested process and verifies all outputs:

```bash
uv --config-file uv.toml run --frozen metaproc run-process \
  process/self-test/test-local.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=self-test-local
```

See
[process/self-test/test-local.process.md](../../process/self-test/test-local.process.md)
for step details.

## Tier 4 — cloud planning (`self-test-cloud-plan`)

Renders a GCP Batch job without submitting it.
This validates the optional cloud dependencies, project/image inputs, and job
construction while remaining free and consumer-independent.

```bash
uv --config-file uv.toml run --frozen metaproc run-process \
  process/self-test/test-cloud.process.md \
  --var RUNS_DIR="$(pwd)/.runs" \
  --var RUN_ID=self-test-cloud-plan \
  --var GCP_PROJECT=your-project \
  --var IMAGE=us-central1-docker.pkg.dev/your-project/tools/metaproc:latest
```

See
[process/self-test/test-cloud.process.md](../../process/self-test/test-cloud.process.md)
for step details.

## Standalone pytest / lint

The same checks that `smoke-core` orchestrates can be invoked directly during iteration:

```bash
make lint-check
make test
```

The `-q` flag suppresses per-test output; drop it to see individual test names.
Add `-k <pattern>` to run a subset.

## Known harness quirks

- `auth-check --live --variant codex-cli` requires the codex permission-mode default set
  by the harness. Fixed in
  [commands/auth_check.py](../../src/metaproc/commands/auth_check.py) — earlier commits
  raised `ValueError` at dispatch time.
- `auth-check --variant <non-claude>` correctly scopes Phase 2b out; setting
  `METAPROC_GCP_SECRET_CLAUDE_CREDS` alongside a non-claude variant no longer pollutes
  the per-adapter signal.
- `--assert-model` is informational-only for `codex-cli`. codex-cli 0.124.0’s JSONL
  stream does not carry a model ID, so the assertion’s “pass” line indicates the `-m`
  flag was accepted (the model guarantee for codex comes from the separate
  negative-control smoke, not from stream parsing).
  See
  [`smoke-adapters-negative-control.process.md`](../../process/self-test/smoke-adapters-negative-control.process.md).
- `--max-concurrency` is not yet honored for sibling code-mode steps.
  Composite children already parallelize, so this only affects code-mode aggregators.

## Future Considerations

### Open Questions

- `--max-concurrency` is not yet honored for sibling code-mode steps in the engine.
  Does this affect `smoke-core` wall clock when run with `--max-concurrency 4`?
  (Composite children in `smoke-adapters-all` already parallelize, so only code-mode
  aggregators are affected.)
- The codex-cli JSONL stream still does not carry a model ID. If a future codex release
  adds one, `--assert-model` for codex should move from informational to hard assertion.
- A live, standalone cloud execution smoke still needs a published image and an
  operator-provided GCP project.
  The committed cloud tier intentionally stops at job rendering so repository
  verification never creates infrastructure or spend.

### Potential Improvements

- Promote the negative-control smoke
  ([`smoke-adapters-negative-control.process.md`](../../process/self-test/smoke-adapters-negative-control.process.md))
  into the tier table as its own row, now that it has landed and covers all four
  adapters.
- Add a `smoke-softschema` tier that runs the no-token softschema-validation runbook as
  a process spec, slotting between `smoke-core` and the per-adapter smokes.
- Track wall-clock actuals in CI (once CI exists) to keep the tier-table estimates
  honest; current numbers are single-laptop observations.

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
