---
process:
  name: smoke-execution-profiles
  description: >-
    Cross-harness "deep" smoke: runs
    `metaproc probe-tool-use --execution-profile <name>` for selected
    execution profile shipped in
    `src/metaproc/data/execution-profiles.default.yaml`. Each
    cell exercises (a) execution-profile registry resolution, (b) adapter
    dispatch with the profile's full `config` block, and (c) a single
    file-read tool round-trip with a sentinel verification.

    Pairs with `smoke-adapters-all` (light smoke: cred survey + trivial
    text prompt per adapter) and `smoke-gemini-matrix` (Gemini-specific
    model coverage). This smoke is the operator-facing readiness gate:
    a green run verifies each covered profile is ready to be
    used by `--execution-profiles <name>` from any dispatch entry point
    (large workflow `dispatch_control.py`, ad-hoc `metaproc run-process`, etc.) with
    tool use intact.

    All cells run in parallel. Each takes ~5-30s. Total wall clock is
    dominated by the slowest profile (typically a Pro-class model).

  steps:
    - id: claude-opus
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; unset METAPROC_GCP_SECRET_CLAUDE_CREDS && uv run metaproc probe-tool-use --execution-profile claude-opus --timeout 120"
      description: >-
        Profile claude-opus (claude-code-cli + opus). Unsets
        METAPROC_GCP_SECRET_CLAUDE_CREDS so the local keychain OAuth path
        is exercised, not the cloud-secret path.

    - id: claude-sonnet
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; unset METAPROC_GCP_SECRET_CLAUDE_CREDS && uv run metaproc probe-tool-use --execution-profile claude-sonnet --timeout 90"
      description: Profile claude-sonnet (claude-code-cli + sonnet).

    - id: codex-gpt61
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; uv run metaproc probe-tool-use --execution-profile codex-gpt61 --timeout 120"
      description: Profile codex-gpt61 (codex-cli + gpt-6.1-sol), the codex-cli default model.

    - id: codex-gpt55
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; uv run metaproc probe-tool-use --execution-profile codex-gpt55 --timeout 120"
      description: Profile codex-gpt55 (codex-cli + gpt-5.5). API-key route only: Codex retired gpt-5.5 for ChatGPT sign-in on 2026-10-14.

    - id: pi-glm52
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile pi-glm52 --timeout 120"
      description: >-
        Profile pi-glm52 (pi-cli + glm-5.2-maas via vertex-maas). GLM 5.2
        capabilities declare native_tools=[Read,Write] only — the probe's
        file-read tool call exercises the Read subset.

    - id: pi-gpt61
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; uv run metaproc probe-tool-use --execution-profile pi-gpt61 --timeout 120"
      description: Profile pi-gpt61 (pi-cli + gpt-6.1-sol via openai).

    - id: pi-gpt55
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; uv run metaproc probe-tool-use --execution-profile pi-gpt55 --timeout 120"
      description: Profile pi-gpt55 (pi-cli + gpt-5.5 via openai).

    - id: gemini-flash-38
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile gemini-flash-38 --timeout 120"
      description: Profile gemini-flash-38 (gemini-cli + gemini-3.8-flash).

    - id: gemini-hydrogen
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile gemini-hydrogen --timeout 120"
      description: Profile gemini-hydrogen (gemini-cli + gemini-4-hydrogen), the Flash-class Gemini 4 lane.

    - id: gemini-argon
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile gemini-argon --timeout 180"
      description: Profile gemini-argon (gemini-cli + gemini-4-argon).

    - id: gemini-carbon
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile gemini-carbon --timeout 180"
      description: Profile gemini-carbon (gemini-cli + gemini-4-carbon). Pro tier — slower, deeper reasoning.

    - id: gemini-pro
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile gemini-pro --timeout 180"
      description: Profile gemini-pro (gemini-cli + gemini-3.1-pro-preview). Pro tier — slower, deeper reasoning.

    - id: pi-gemini-hydrogen
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile pi-gemini-hydrogen --timeout 120"
      description: >-
        Profile pi-gemini-hydrogen (pi-cli + gemini-4-hydrogen via
        google-vertex). Mirrors gemini-hydrogen but uses the pi-cli harness
        to exercise the google-vertex API path's thought_signature contract.

    - id: pi-gemini-pro
      mode: code
      command: >-
        bash -lc "cd ../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc probe-tool-use --execution-profile pi-gemini-pro --timeout 180"
      description: Profile pi-gemini-pro (pi-cli + gemini-3.1-pro-preview via google-vertex).
---
# smoke-execution-profiles — deep tool-use smoke for execution profiles

The operator-facing readiness gate for execution profiles.
The configured cells cover selected profiles from
[`execution-profiles.default.yaml`](../../src/metaproc/data/execution-profiles.default.yaml)
using `probe-tool-use --execution-profile <name>`. Each probe checks profile resolution
and end-to-end tool use.

## Where this sits in the smoke ladder

| Smoke | Tests | Token cost |
| --- | --- | --- |
| `smoke-core` | lint + unit tests, no model calls | $0 |
| `smoke-adapters-all` (light) | per-adapter cred survey + trivial text prompt | ~6 prompts |
| `smoke-adapters-negative-control` | bogus model rejection per adapter | ~4 prompts |
| **`smoke-execution-profiles`** (this) | **profile resolution + tool round-trip per covered profile** | **One prompt per cell** |
| `smoke-gemini-matrix` | model identity and tool round-trip for selected Gemini routes | Two prompts per cell |

The light smoke (`smoke-adapters-all`) tells you “creds work”.
This smoke tells you “the profile is dispatch-ready including tool use”.
The matrix smoke also checks model identity for covered Gemini routes.

## Cells

| Cell | Adapter | Model |
| --- | --- | --- |
| `claude-opus` | claude-code-cli | opus |
| `claude-sonnet` | claude-code-cli | sonnet |
| `codex-gpt61` | codex-cli | gpt-6.1-sol |
| `codex-gpt55` | codex-cli | gpt-5.5 (API key only) |
| `pi-glm52` | pi-cli | glm-5.2-maas (vertex-maas) |
| `pi-gpt61` | pi-cli | gpt-6.1-sol (openai) |
| `pi-gpt55` | pi-cli | gpt-5.5 (openai) |
| `gemini-flash-38` | gemini-cli | gemini-3.8-flash |
| `gemini-hydrogen` | gemini-cli | gemini-4-hydrogen |
| `gemini-argon` | gemini-cli | gemini-4-argon |
| `gemini-carbon` | gemini-cli | gemini-4-carbon |
| `gemini-pro` | gemini-cli | gemini-3.1-pro-preview |
| `pi-gemini-hydrogen` | pi-cli | gemini-4-hydrogen (google-vertex) |
| `pi-gemini-pro` | pi-cli | gemini-3.1-pro-preview (google-vertex) |

The operator reference shows how to
[compare and verify Gemini profiles](../../src/metaproc/docs/metaproc-operator-reference.md#compare-execution-profiles),
including `gemini-neon` and `gemini-flash-36` in eligible Vertex projects.

## Credential requirements

Union of what each cell needs — see
[`credential-setup.runbook.md`](../../src/metaproc/docs/credential-setup.runbook.md):

| Cell | Credential |
| --- | --- |
| `claude-opus` / `claude-sonnet` | macOS Keychain OAuth (`claude login`) or `ANTHROPIC_API_KEY` |
| `codex-gpt61` | `OPENAI_API_KEY` or `~/.codex/auth.json` |
| `codex-gpt55` | `OPENAI_API_KEY` (ChatGPT sign-in no longer serves gpt-5.5) |
| `pi-gpt61` / `pi-gpt55` | `OPENAI_API_KEY` |
| `pi-glm52` | `~/.pi/auth.json` + GCP ADC for Vertex MaaS |
| `gemini-*` | GCP ADC for Vertex AI + `METAPROC_GCP_PROJECT` |
| `pi-gemini-hydrogen` / `pi-gemini-pro` | `~/.pi/auth.json` + GCP ADC for google-vertex API |

A missing cred reds only the affected cell; the others stay green.

## Usage

```bash
# Run all configured cells
uv run metaproc run-process process/self-test/smoke-execution-profiles.process.md

# Single profile (debugging a red)
uv run metaproc probe-tool-use --execution-profile gemini-hydrogen --timeout 120
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
[large workflow tier.process.md](../../README.md#process-specs)). A smoke that bypasses
profile resolution (e.g. `--adapter` + `--model` directly) proves the model works but
not that the profile-driven dispatch path works.
This smoke closes that gap.
