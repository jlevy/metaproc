---
process:
  name: smoke-execution-profiles
  description: >-
    Cross-harness "deep" smoke: runs
    `metaproc probe-tool-use --execution-profile <name>` for every
    execution profile shipped in
    `src/metaproc/data/execution-profiles.default.yaml`. Each
    cell exercises (a) execution-profile registry resolution, (b) adapter
    dispatch with the profile's full `config` block, and (c) a single
    file-read tool round-trip with a sentinel verification.

    Pairs with `smoke-adapters-all` (light smoke: cred survey + trivial
    text prompt per adapter) and `smoke-gemini-matrix` (Gemini-specific
    model coverage). This smoke is the operator-facing readiness gate:
    a green run proves every shipped execution profile is ready to be
    used by `--execution-profiles <name>` from any dispatch entry point
    (EIA `dispatch_control.py`, ad-hoc `metaproc run-process`, etc.) with
    tool use intact.

    All cells run in parallel. Each takes ~5-30s. Total wall clock is
    dominated by the slowest profile (typically a Pro-class model).

  steps:
    - id: claude-opus
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; unset METAPROC_GCP_SECRET_CLAUDE_CREDS && uv run metaproc probe-tool-use --execution-profile claude-opus --timeout 120"
      description: >-
        Profile claude-opus (claude-code-cli + opus). Unsets
        METAPROC_GCP_SECRET_CLAUDE_CREDS so the local keychain OAuth path
        is exercised, not the cloud-secret path.

    - id: claude-sonnet
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; unset METAPROC_GCP_SECRET_CLAUDE_CREDS && uv run metaproc probe-tool-use --execution-profile claude-sonnet --timeout 90"
      description: Profile claude-sonnet (claude-code-cli + sonnet).

    - id: codex-gpt55
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; uv run metaproc probe-tool-use --execution-profile codex-gpt55 --timeout 120"
      description: Profile codex-gpt55 (codex-cli + gpt-5.5).

    - id: pi-glm5
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile pi-glm5 --timeout 120"
      description: >-
        Profile pi-glm5 (pi-cli + glm-5-maas via vertex-maas). GLM-5
        capabilities declare native_tools=[Read,Write] only — the probe's
        file-read tool call exercises the Read subset.

    - id: gemini-flash
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile gemini-flash --timeout 120"
      description: Profile gemini-flash (gemini-cli + gemini-3.5-flash).

    - id: gemini-pro
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile gemini-pro --timeout 180"
      description: Profile gemini-pro (gemini-cli + gemini-3.1-pro-preview). Pro tier — slower, deeper reasoning.

    - id: pi-gemini-flash
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile pi-gemini-flash --timeout 120"
      description: >-
        Profile pi-gemini-flash (pi-cli + gemini-3.5-flash via google-vertex).
        Mirrors gemini-flash but uses the pi-cli harness to exercise the
        google-vertex API path's Gemini-3 thought_signature contract.
---
# smoke-execution-profiles — deep tool-use smoke across every shipped profile

The operator-facing readiness gate for execution profiles.
Every profile in
[`execution-profiles.default.yaml`](../../src/metaproc/data/execution-profiles.default.yaml)
gets one cell that runs `probe-tool-use --execution-profile <name>`. A green run proves
the profile can be referenced via `--execution-profiles <name>` from any dispatch entry
point with tool use working end-to-end.

## Where this sits in the smoke ladder

| Smoke | Tests | Token cost |
| --- | --- | --- |
| `smoke-core` | lint + unit tests, no model calls | $0 |
| `smoke-adapters-all` (light) | per-adapter cred survey + trivial text prompt | ~6 prompts |
| `smoke-adapters-negative-control` | bogus model rejection per adapter | ~4 prompts |
| **`smoke-execution-profiles`** (this) | **profile resolution + tool round-trip per profile** | **~N prompts (N = profile count)** |
| `smoke-gemini-matrix` | every Gemini model × both harnesses with tool round-trip | ~9 prompts |

The light smoke (`smoke-adapters-all`) tells you “creds work”.
This smoke tells you “the profile is dispatch-ready including tool use”.
The matrix smoke tells you “every model variant works on its harness”.

## Cells

One cell per profile in
[`execution-profiles.default.yaml`](../../src/metaproc/data/execution-profiles.default.yaml):

| Cell | Adapter | Model |
| --- | --- | --- |
| `claude-opus` | claude-code-cli | opus |
| `claude-sonnet` | claude-code-cli | sonnet |
| `codex-gpt55` | codex-cli | gpt-5.5 |
| `pi-glm5` | pi-cli | glm-5-maas (vertex-maas) |
| `gemini-flash` | gemini-cli | gemini-3.5-flash |
| `gemini-pro` | gemini-cli | gemini-3.1-pro-preview |
| `pi-gemini-flash` | pi-cli | gemini-3.5-flash (google-vertex) |

When a new execution profile is added to the default registry, add a cell here.
(A future enhancement: dynamically enumerate profiles from the registry so this list
stays in sync automatically.
For now it is hand-maintained.)

## Credential requirements

Union of what each cell needs — see
[`credential-setup.runbook.md`](../../docs/runbooks/credential-setup.runbook.md):

| Cell | Credential |
| --- | --- |
| `claude-opus` / `claude-sonnet` | macOS Keychain OAuth (`claude login`) or `ANTHROPIC_API_KEY` |
| `codex-gpt55` | `OPENAI_API_KEY` or `~/.codex/auth.json` |
| `pi-glm5` | `~/.pi/auth.json` + GCP ADC for Vertex MaaS |
| `gemini-flash` / `gemini-pro` | GCP ADC for Vertex AI + `METAPROC_GCP_PROJECT` |
| `pi-gemini-flash` | `~/.pi/auth.json` + GCP ADC for google-vertex API |

A missing cred reds only the affected cell; the others stay green.

## Usage

```bash
# Full readiness gate across every shipped profile
uv run metaproc run-process process/self-test/smoke-execution-profiles.process.md

# Single profile (debugging a red)
uv run metaproc probe-tool-use --execution-profile gemini-flash --timeout 120
```

## When this is red

Each cell is independent — a red cell points at exactly one `(profile, adapter, model)`
combination that is not dispatch-ready.

- **Probe fails with sentinel-not-found** — the model received the prompt but did not
  complete the tool round-trip.
  Could be a model that hallucinated instead of calling the read tool, or a
  harness-level tool-call regression (e.g. Gemini-3 `thought_signature` if the profile
  routes through a non-google-vertex pi API).
- **Probe fails with auth error** — the cred for that adapter is missing or expired.
  Run the per-adapter smoke (`smoke-adapter-<name>.process.md`) for a tighter
  diagnostic.
- **Probe fails with “unknown execution profile”** — the profile name is not in the
  registry; check
  [`execution-profiles.default.yaml`](../../src/metaproc/data/execution-profiles.default.yaml).

Last verified: 2026-05-25 — all 6 cells PASS (wall clock: 48s).

## Why this is the right shape for a workflow smoke

The user-facing operator surface is `--execution-profiles <name>` (per
[EIA tier.process.md](../../README.md#process-specs)). A smoke that bypasses profile
resolution (e.g. `--adapter` + `--model` directly) proves the model works but not that
the profile-driven dispatch path works.
This smoke closes that gap.
