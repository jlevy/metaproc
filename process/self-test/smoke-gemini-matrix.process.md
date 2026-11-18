---
process:
  name: smoke-gemini-matrix
  description: >-
    Live smoke matrix across the Gemini family on both harnesses. Each
    cell chains `auth-check --live --adapter <h> --model <m> --assert-model
    <m>` with `probe-tool-use --harness <h> --model <m>` so a cell is
    green only when (a) the model dispatches against the requested ID and
    (b) a single file-read tool round-trip succeeds with the sentinel echoed
    in the response. All cells run in parallel — wall clock is dominated
    by the slowest cell.

    A green run proves every covered Gemini model dispatches end-to-end
    through both harnesses with tool use intact — including catching the
    Gemini-3 `thought_signature` regression class that text-only probes
    miss.

  steps:
    # ── gemini-cli cells (7 models) ──
    - id: gemini-cli-4-carbon
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-4-carbon --assert-model gemini-4-carbon && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-4-carbon --timeout 180"
      description: gemini-cli + gemini-4-carbon (GA 2026-11-10). Strongest Gemini 4 model.

    - id: gemini-cli-4-argon
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-4-argon --assert-model gemini-4-argon && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-4-argon --timeout 180"
      description: gemini-cli + gemini-4-argon (GA 2026-10-13). Vertex availability until at least 2027-10-13.

    - id: gemini-cli-4-hydrogen
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-4-hydrogen --assert-model gemini-4-hydrogen && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-4-hydrogen --timeout 120"
      description: gemini-cli + gemini-4-hydrogen (GA 2026-11-12). Google's recommended 3.8 Flash successor.

    - id: gemini-cli-3.8-flash
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-3.8-flash --assert-model gemini-3.8-flash && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-3.8-flash --timeout 120"
      description: gemini-cli + gemini-3.8-flash (GA 2026-09-02). Also the target of the flash alias.

    - id: gemini-cli-3.5-flash-lite
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-3.5-flash-lite --assert-model gemini-3.5-flash-lite && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-3.5-flash-lite --timeout 120"
      description: gemini-cli + gemini-3.5-flash-lite (GA 2026-07-21). Also the target of the flash-lite alias.

    - id: gemini-cli-3.1-pro-preview
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-3.1-pro-preview --assert-model gemini-3.1-pro-preview && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-3.1-pro-preview --timeout 180"
      description: gemini-cli + gemini-3.1-pro-preview. Deeper-reasoning preview lane.

    - id: gemini-cli-3.1-pro-preview-customtools
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-3.1-pro-preview-customtools --assert-model gemini-3.1-pro-preview-customtools && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-3.1-pro-preview-customtools --timeout 180"
      description: gemini-cli + gemini-3.1-pro-preview-customtools. The gemini-cli default.

    # ── pi-cli (google-vertex) cells (4 models: gemini-4-hydrogen from pi-models.default.json, the rest Pi built-ins) ──
    - id: pi-cli-4-hydrogen
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter pi-cli --model gemini-4-hydrogen --provider google-vertex --assert-model gemini-4-hydrogen && uv run metaproc probe-tool-use --harness pi-cli --provider google-vertex --model gemini-4-hydrogen --timeout 120"
      description: pi-cli (google-vertex) + gemini-4-hydrogen. Validates the google-vertex API path.

    - id: pi-cli-4-argon
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter pi-cli --model gemini-4-argon --provider google-vertex --assert-model gemini-4-argon && uv run metaproc probe-tool-use --harness pi-cli --provider google-vertex --model gemini-4-argon --timeout 180"
      description: pi-cli (google-vertex) + gemini-4-argon.

    - id: pi-cli-3.8-flash
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter pi-cli --model gemini-3.8-flash --provider google-vertex --assert-model gemini-3.8-flash && uv run metaproc probe-tool-use --harness pi-cli --provider google-vertex --model gemini-3.8-flash --timeout 120"
      description: pi-cli (google-vertex) + gemini-3.8-flash.

    - id: pi-cli-3.1-pro-preview
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter pi-cli --model gemini-3.1-pro-preview --provider google-vertex --assert-model gemini-3.1-pro-preview && uv run metaproc probe-tool-use --harness pi-cli --provider google-vertex --model gemini-3.1-pro-preview --timeout 180"
      description: pi-cli (google-vertex) + gemini-3.1-pro-preview.
---
# smoke-gemini-matrix — full Gemini coverage matrix across both harnesses

Live matrix that proves every registered Gemini model dispatches end-to-end through both
the `gemini-cli` and `pi-cli` harnesses with tool use intact.
All cells run in parallel; the slowest cell determines wall clock (~5–30s depending on
model).

## What each cell proves

For a given `(harness, model)`:

1. **Auth + dispatch** —
   `metaproc auth-check --live --adapter <h> --model <m> --assert-model <m>` issues a
   trivial prompt and verifies the model the harness reports matches the requested one,
   which catches silent fallback to the harness default.
   For `gemini-cli` that is an exact match on a model the terminal `result` event’s
   `stats.models` shows billing tokens; for `pi-cli`, a substring match on
   `message.model` in the first assistant `message_start` event.
2. **Tool round-trip** — `metaproc probe-tool-use --harness <h> --model <m>` writes a
   unique sentinel string to a tempfile under `.metaproc-probe/<hash>/`, asks the model
   to read it, and asserts the sentinel appears in the model’s response (parsing
   stream-json chunked deltas so split tokens still match).
   The sentinel is high-entropy hex so the model cannot hallucinate it.

The cell is green only if both pass.
This is the test that catches the Gemini-3 `thought_signature` regression class — a
model that text-generates fine but cannot complete a tool call will fail step 2 even
when step 1 is green.

## Matrix

| Harness | gemini-4-carbon | gemini-4-argon | gemini-4-hydrogen | gemini-3.8-flash | gemini-3.5-flash-lite | gemini-3.1-pro-preview | gemini-3.1-pro-preview-customtools |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gemini-cli` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `pi-cli` (google-vertex) | — | ✓ | ✓ | ✓ | — | ✓ | — |

The gemini-cli row covers every concrete model the catalog accepts for that harness
except `gemini-3.6-flash`, which Vertex retires on 2026-11-19 and which remains
reachable only through the Gemini API.
The pi-cli row covers one model per Gemini lane; the other `google-vertex` models use
the same API path.
The pi-cli `--assert-model` check is a substring match, so its `gemini-3.1-pro-preview`
cell would also pass on `gemini-3.1-pro-preview-customtools`.

## Credential requirements

- `~/.config/gcloud/application_default_credentials.json` from
  `gcloud auth application-default login`, OR `GCP_CREDENTIALS_BASE64` in `.env`
  (auto-materialized to a temp file by `metaproc.cloud.gcp.gcp_credentials`).
- `GOOGLE_GENAI_USE_VERTEXAI=true` (gemini-cli only).
- `GOOGLE_CLOUD_PROJECT` (or `METAPROC_GCP_PROJECT` from `.env`).
- `GOOGLE_CLOUD_LOCATION=global` — regional values fail the native `google-vertex` API
  path (see
  [adapter-compatibility runbook §3](../../docs/runbooks/adapter-compatibility.runbook.md)).
- The pi-cli cells need no `~/.pi/agent/auth.json`: the `google-vertex` provider uses
  the same Application Default Credentials.

No `GEMINI_API_KEY` or `GOOGLE_API_KEY` required.

## Why all cells in parallel

Each cell is independent.
The per-cell `bash -lc` chains `auth-check --live && probe-tool-use` so a failure in
step 1 short-circuits step 2 and the cell as a whole reds with the right diagnostic
surface.

## Usage

```bash
# Full matrix
uv run metaproc run-process process/self-test/smoke-gemini-matrix.process.md

# Single cell (for debugging a red)
uv run metaproc run-process process/self-test/smoke-gemini-matrix.process.md \
  --only gemini-cli-4-hydrogen
```

## When this is red

A red cell points at exactly one `(harness, model)` combination.

- **auth-check stage red** — the harness binary, credentials, or model registration is
  the problem. Run the per-adapter smoke with `<MODEL>_SMOKE_MODEL` set to the same
  value, or invoke `metaproc auth-check --live --adapter <h> --model <m>` directly
  outside the process runner.
- **probe-tool-use stage red** — auth works but the tool round-trip is broken.
  This is the `thought_signature` class of bug for Gemini-3 on pi-cli, or a model that
  hallucinated instead of calling the read tool.
  The probe prints the first 2 KB of subprocess stdout on failure to inspect what the
  model produced.

Last verified on Vertex: 2026-05-25, with the earlier model set — see
[adapter-compatibility runbook §8](../../docs/runbooks/adapter-compatibility.runbook.md).
The current cells have not been run on Vertex; the gemini-cli models passed a
one-tool-call probe through gemini-cli 0.66.0 on the Gemini API on 2026-11-18.
