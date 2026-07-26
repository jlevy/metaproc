---
process:
  name: smoke-adapters-all
  description: >-
    End-to-end live smoke across every registered adapter. Runs the
    provider-agnostic `auth-check` dry survey plus the four per-adapter
    smokes (claude, codex, gemini, pi) in parallel. Each child smoke
    verifies the binary is on PATH, credentials are detectable, a
    trivial prompt round-trips through the real backend, and
    `--assert-model` confirms the CLI dispatched against the expected
    model (informational only for codex — see
    `smoke-adapter-codex.process.md`). A green run here means every
    adapter can dispatch laptop-local today and that no silent
    `--model` fallback is lurking in any of them.

  deps:
    smoke_adapter_claude:
      path: "./smoke-adapter-claude.process.md"
      as: path
    smoke_adapter_codex:
      path: "./smoke-adapter-codex.process.md"
      as: path
    smoke_adapter_gemini:
      path: "./smoke-adapter-gemini.process.md"
      as: path
    smoke_adapter_pi:
      path: "./smoke-adapter-pi.process.md"
      as: path

  steps:
    - id: auth-check-survey
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; unset METAPROC_GCP_SECRET_CLAUDE_CREDS && export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check"
      description: >-
        Phase 1 dry survey across all registered adapters (disk space,
        gcloud token, each adapter's binary + credential). No prompts
        dispatched. Explicitly unsets `METAPROC_GCP_SECRET_CLAUDE_CREDS`
        (same reasoning as the per-adapter claude smoke) so a stale
        cloud-dispatch credential does not red this aggregator when the
        local auth path is fine. Exports Vertex AI env vars so gemini's
        credential-detection path lands in ADC mode rather than
        red-flagging because no GEMINI_API_KEY is set.

    - id: claude
      mode: composite
      uses: deps.smoke_adapter_claude
      description: Live binary + credential + model-asserted prompt for claude-code-cli.

    - id: codex
      mode: composite
      uses: deps.smoke_adapter_codex
      description: Live binary + credential + model-asserted prompt for codex-cli.

    - id: gemini
      mode: composite
      uses: deps.smoke_adapter_gemini
      description: Live binary + credential + model-asserted prompt for gemini-cli.

    - id: pi
      mode: composite
      uses: deps.smoke_adapter_pi
      description: Live binary + credential + model-asserted prompt for pi-cli (glm-5-maas).
---
# smoke-adapters-all — live aggregator across every adapter

Single process that runs the five laptop-live adapter checks in parallel: `auth-check`
dry survey plus the four per-adapter smokes.
On a green run, every registered adapter in
[`ADAPTER_REGISTRY`](../../src/metaproc/adapters/registry.py) has been shown to dispatch
against the expected model end-to-end.

## Why an aggregator

The per-adapter smokes already give adapter-scoped red signals (you know exactly which
CLI / credential / model is broken when one fails).
This file is the pre-commit / pre-merge confidence gate: one command and if it’s green
every live dispatch path works today.

The four per-adapter smokes are `mode: composite` children so the engine orchestrates
them directly (each with its own run sub-directory, logs, and state), rather than
shelling out through a second `metaproc run-process` process.

## What is NOT covered

- **Cloud dispatch (GCP Batch)** — see [`test-cloud.process.md`](test-cloud.process.md).
  The aggregator stays laptop-local so it runs quickly and has no GCP build / Filestore
  cost.

- **Phase 2b Secret Manager cloud credential** — intentional.
  The per-adapter smokes unset `METAPROC_GCP_SECRET_CLAUDE_CREDS` so a stale cloud OAuth
  blob does not red laptop checks.
  Probe the Secret Manager credential directly:

  ```bash
  uv run metaproc auth-check --live --variant claude-code-cli \
    --claude-secret-ref projects/<P>/secrets/<N>/versions/latest
  ```

- **Negative-control smoke** (bogus model name → red) — see
  `smoke-adapters-negative-control.process.md` once it lands (`internal-reference`).
  That is the codex-side guarantee, since codex-cli’s JSONL stream does not carry a
  model ID and cannot be verified via `--assert-model` alone.

- **Full mine pipeline** — see [`test-local.process.md`](test-local.process.md).

## Credential requirements

Sum of what each child needs.
None are installed by this process; see
[`../../docs/runbooks/credential-setup.runbook.md`](../../docs/runbooks/credential-setup.runbook.md).

| Adapter | Credential |
| --- | --- |
| `claude-code-cli` | macOS Keychain OAuth (`claude login`) or `ANTHROPIC_API_KEY` |
| `codex-cli` | `OPENAI_API_KEY` or `~/.codex/auth.json` from `codex login` |
| `gemini-cli` | GCP ADC (`gcloud auth application-default login`) + `METAPROC_GCP_PROJECT` (already in `.env`) |
| `pi-cli` | `~/.pi/auth.json` + GCP ADC for Vertex MaaS |

## Usage

```bash
# Full aggregator
uv run metaproc run-process process/self-test/smoke-adapters-all.process.md

# Skip one adapter if you know its cred is down
uv run metaproc run-process process/self-test/smoke-adapters-all.process.md \
  --skip claude
```

## When this is red

Each composite child’s logs land under its own run dir.
Re-run the specific smoke in isolation for the tightest diagnostic:

```bash
uv run metaproc run-process process/self-test/smoke-adapter-<name>.process.md
```
