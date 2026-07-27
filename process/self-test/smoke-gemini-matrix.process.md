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
    # ── gemini-cli cells (5 models — latest + previews) ──
    - id: gemini-cli-3.5-flash
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-3.5-flash --assert-model gemini-3.5-flash && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-3.5-flash --timeout 120"
      description: gemini-cli + gemini-3.5-flash (GA 2026-05-19, Google I/O).

    - id: gemini-cli-3.1-pro-preview
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-3.1-pro-preview --assert-model gemini-3.1-pro-preview && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-3.1-pro-preview --timeout 180"
      description: gemini-cli + gemini-3.1-pro-preview. Deeper-reasoning lane.

    - id: gemini-cli-3.1-flash-lite
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-3.1-flash-lite --assert-model gemini-3.1-flash-lite && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-3.1-flash-lite --timeout 120"
      description: gemini-cli + gemini-3.1-flash-lite. Smallest 3.1-class model.

    - id: gemini-cli-3-flash-preview
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-3-flash-preview --assert-model gemini-3-flash-preview && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-3-flash-preview --timeout 120"
      description: gemini-cli + gemini-3-flash-preview. Earlier 3-class Flash for cross-version comparisons.

    - id: gemini-cli-3-pro-preview
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter gemini-cli --model gemini-3-pro-preview --assert-model gemini-3-pro-preview && uv run metaproc probe-tool-use --harness gemini-cli --model gemini-3-pro-preview --timeout 180"
      description: gemini-cli + gemini-3-pro-preview. Earlier 3-class Pro for cross-version comparisons.

    # ── pi-cli (google-vertex) cells (4 models registered in pi-models.default.json) ──
    - id: pi-cli-3.5-flash
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter pi-cli --model gemini-3.5-flash --provider google-vertex --assert-model gemini-3.5-flash && uv run metaproc probe-tool-use --harness pi-cli --provider google-vertex --model gemini-3.5-flash --timeout 120"
      description: pi-cli (google-vertex) + gemini-3.5-flash. Validates google-vertex API path.

    - id: pi-cli-3.1-pro-preview
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter pi-cli --model gemini-3.1-pro-preview --provider google-vertex --assert-model gemini-3.1-pro-preview && uv run metaproc probe-tool-use --harness pi-cli --provider google-vertex --model gemini-3.1-pro-preview --timeout 180"
      description: pi-cli (google-vertex) + gemini-3.1-pro-preview.

    - id: pi-cli-3.1-flash-lite
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter pi-cli --model gemini-3.1-flash-lite --provider google-vertex --assert-model gemini-3.1-flash-lite && uv run metaproc probe-tool-use --harness pi-cli --provider google-vertex --model gemini-3.1-flash-lite --timeout 120"
      description: pi-cli (google-vertex) + gemini-3.1-flash-lite.

    - id: pi-cli-3-flash-preview
      mode: code
      command: >-
        bash -lc "cd ../../.. && [ -f .env ] && set -a && source .env && set +a; export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && uv run metaproc auth-check --live --adapter pi-cli --model gemini-3-flash-preview --provider google-vertex --assert-model gemini-3-flash-preview && uv run metaproc probe-tool-use --harness pi-cli --provider google-vertex --model gemini-3-flash-preview --timeout 120"
      description: pi-cli (google-vertex) + gemini-3-flash-preview. Earlier 3-class on the google-vertex path.
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
   trivial prompt and verifies the observed model ID in the harness’s identity event
   matches the requested one (substring match; passing the bare model ID is tight enough
   to catch silent fallback to the harness default).
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

| Harness | gemini-3.5-flash | gemini-3.1-pro-preview | gemini-3.1-flash-lite | gemini-3-flash-preview | gemini-3-pro-preview |
| --- | --- | --- | --- | --- | --- |
| `gemini-cli` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `pi-cli` (google-vertex) | ✓ | ✓ | ✓ | ✓ | — (not in pi-models.default.json) |

`gemini-3.1-flash` (non-lite) is not registered in either allowlist; only the `-lite`
variant ships. `gemini-3-pro-preview` is registered for gemini-cli but not pi-cli’s
`google-vertex` provider in `pi-models.default.json`, so it’s absent from the pi-cli
row.

## Credential requirements

- `~/.config/gcloud/application_default_credentials.json` from
  `gcloud auth application-default login`, OR `GCP_CREDENTIALS_BASE64` in `.env`
  (auto-materialized to a temp file by `metaproc.cloud.gcp.gcp_credentials`).
- `GOOGLE_GENAI_USE_VERTEXAI=true` (gemini-cli only).
- `GOOGLE_CLOUD_PROJECT` (or `METAPROC_GCP_PROJECT` from `.env`).
- `GOOGLE_CLOUD_LOCATION=global` — regional values fail the native `google-vertex` API
  path (see
  [adapter-compatibility runbook §3](../../docs/runbooks/adapter-compatibility.runbook.md)).
- `~/.pi/auth.json` from `pi auth` (pi-cli cells only).

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
  --only gemini-cli-3.5-flash
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

Last verified: 2026-05-25 — see
[adapter-compatibility runbook §8](../../docs/runbooks/adapter-compatibility.runbook.md).
