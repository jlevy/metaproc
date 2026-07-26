---
process:
  name: smoke-adapter-gemini
  description: >-
    Per-adapter smoke for gemini-cli. Confirms the binary is on PATH, a
    credential source is detectable, and a trivial prompt round-trips
    through the real Gemini backend. Uses Vertex AI mode with GCP
    Application Default Credentials (ADC); the project ID comes from
    `METAPROC_GCP_PROJECT` (already in `.env`). No Gemini-specific API
    key is needed. See the body for why this smoke isolates HOME.

  steps:
    - id: binary-check
      mode: code
      command: >-
        bash -lc "gemini --version"
      description: Confirm the gemini-cli binary is installed and on PATH.

    - id: auth-check-dry
      mode: code
      command: >-
        bash -lc "export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && cd ../../.. && uv run metaproc auth-check --variant gemini-cli"
      description: >-
        Dry survey in Vertex AI + ADC mode. `GOOGLE_GENAI_USE_VERTEXAI=true`
        switches the adapter into Vertex mode; `GOOGLE_CLOUD_PROJECT`
        points at the project already declared for GCP Batch dispatch.
        No `GEMINI_API_KEY` or `GOOGLE_API_KEY` needed.
      needs: [binary-check]

    - id: live-probe
      mode: code
      command: >-
        bash -lc "GEMINI_ORIG_HOME=\"$HOME\" && export HOME=\"$(mktemp -d -t gemini-smoke-XXXXXX)\" && trap 'rm -rf \"$HOME\"' EXIT && export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && export GOOGLE_APPLICATION_CREDENTIALS=\"${GOOGLE_APPLICATION_CREDENTIALS:-$GEMINI_ORIG_HOME/.config/gcloud/application_default_credentials.json}\" && cd \"$GEMINI_ORIG_HOME/wrk/example-org/consumer\" && SMOKE_MODEL=\"${GEMINI_SMOKE_MODEL:-gemini-3.1-pro-preview-customtools}\" && SMOKE_ASSERT=\"${GEMINI_SMOKE_ASSERT_MODEL:-$SMOKE_MODEL}\" && uv run metaproc auth-check --live --variant \"gemini-cli-$SMOKE_MODEL\" --assert-model \"$SMOKE_ASSERT\""
      description: >-
        Send a trivial "Respond with exactly: OK" prompt via
        `gemini -p` and assert the `model` field in the stream-json
        `init` event matches the expected ID. Defaults to
        GEMINI_DEFAULT_MODEL (`gemini-3.1-pro-preview-customtools`)
        with the assertion pinned to that same ID. Override per cell
        by exporting `GEMINI_SMOKE_MODEL=<id>` (and optionally
        `GEMINI_SMOKE_ASSERT_MODEL` if the observed model ID differs
        from the requested one — e.g. Vertex sometimes prefixes
        `models/`). `--assert-model` is substring-matched, so passing
        the full model ID is enough to catch silent fallback to the
        default. Isolates HOME so the operator's persisted
        `~/.gemini/settings.json` (which may pin `selectedType` to
        `gemini-api-key` from a prior interactive login) does not
        override the Vertex AI env-var mode.
        `GOOGLE_APPLICATION_CREDENTIALS` is redirected to the original
        HOME's gcloud ADC file so auth still works inside the isolated
        HOME.
      needs: [auth-check-dry]

    - id: tool-probe
      mode: code
      command: >-
        bash -lc "GEMINI_ORIG_HOME=\"$HOME\" && export HOME=\"$(mktemp -d -t gemini-smoke-XXXXXX)\" && trap 'rm -rf \"$HOME\"' EXIT && export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && export GOOGLE_APPLICATION_CREDENTIALS=\"${GOOGLE_APPLICATION_CREDENTIALS:-$GEMINI_ORIG_HOME/.config/gcloud/application_default_credentials.json}\" && cd \"$GEMINI_ORIG_HOME/wrk/example-org/consumer\" && SMOKE_MODEL=\"${GEMINI_SMOKE_MODEL:-gemini-3.1-pro-preview-customtools}\" && uv run metaproc probe-tool-use --harness gemini-cli --model \"$SMOKE_MODEL\""
      description: >-
        Round-trip a single tool call (file read) through the model
        and verify both that a tool event was emitted and that the
        model's response includes a unique sentinel string from the
        controlled tempfile. Catches the Gemini-3 `thought_signature`
        round-trip class of bug — a model that text-generates fine but
        cannot complete a tool call will fail here even when
        `live-probe` is green. Overridable via `GEMINI_SMOKE_MODEL`.
      needs: [live-probe]
---
# smoke-adapter-gemini — live smoke for gemini-cli

Three-step gate for gemini-cli: binary → credential → live prompt, using **Vertex AI +
ADC**. No Gemini-specific API key required — reuses the GCP credentials already set up
for Batch dispatch and Vertex MaaS.

## Steps

1. **binary-check** — `gemini --version`.
2. **auth-check-dry** — `metaproc auth-check --variant gemini-cli` with
   `GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT=$METAPROC_GCP_PROJECT`.
   Exercises the adapter’s own credential-detection logic.
3. **live-probe** — Isolated-HOME + `gemini -p` against Vertex AI (see below), with
   `--assert-model <model>` to verify the `init` event’s `model` field matches the
   expected ID. Defaults to GEMINI_DEFAULT_MODEL; override with `GEMINI_SMOKE_MODEL` to
   exercise a specific cell of the matrix.
4. **tool-probe** — `metaproc probe-tool-use --harness gemini-cli` issues a single
   file-read tool call and verifies both the tool event and the sentinel-string
   round-trip in the model’s response.
   Catches `thought_signature` regressions where text-gen works but tool use breaks.

## Per-model overrides

To exercise a specific model (Phase 1 matrix), export `GEMINI_SMOKE_MODEL` before
running:

```bash
GEMINI_SMOKE_MODEL=gemini-3.5-flash \
  uv run metaproc run-process process/self-test/smoke-adapter-gemini.process.md
```

`live-probe` will request that model and assert the same ID; `tool-probe` will use the
same model for the file-read round-trip.
If Vertex returns a different observed ID shape (e.g. `models/gemini-3.5-flash-001`),
`--assert-model` does substring matching, so the bare ID is enough — override
`GEMINI_SMOKE_ASSERT_MODEL` only if the substring match needs to differ from the
requested model.

## Auth modes supported by the Gemini adapter

The `gemini` CLI resolves auth from a merged settings chain (`~/.gemini/settings.json`)
that **takes precedence over env vars** — once an operator has run `gemini`
interactively and picked an auth mode, that mode is pinned.
Env-var fallback (`getAuthTypeFromEnv()` in
`@google/gemini-cli-core/contentGenerator.js`) only fires when the settings chain has no
`security.auth.selectedType`.

The five valid `AuthType` values in `@google/gemini-cli-core`:

| Value in settings.json | Env-var trigger | What it does |
| --- | --- | --- |
| `gemini-api-key` | `GEMINI_API_KEY` | Direct Gemini API — personal API key |
| `vertex-ai` | `GOOGLE_GENAI_USE_VERTEXAI=true` | Vertex AI (needs `GOOGLE_CLOUD_PROJECT` + ADC, or `GOOGLE_API_KEY` for Express) |
| `compute-default-credentials` | `GEMINI_CLI_USE_COMPUTE_ADC=true` or `GOOGLE_APPLICATION_CREDENTIALS` | Pure ADC path — GCE metadata or key file |
| `oauth-personal` | `CLOUD_SHELL` | Personal OAuth / Cloud Shell |
| `gateway` | — | Gateway mode |

## Why this smoke isolates HOME

If the operator’s `~/.gemini/settings.json` has
`security.auth.selectedType: "gemini-api-key"` (common: set during a prior `gemini`
interactive login), the CLI refuses to fall back to env-var-driven Vertex AI mode and
exits with “you must specify the GEMINI_API_KEY environment variable”.
Temporary HOME sidesteps that lockout without asking the operator to permanently change
their settings, while `GOOGLE_APPLICATION_CREDENTIALS` pointing back at the real
`~/.config/gcloud/application_default_credentials.json` keeps ADC working.

To permanently switch your personal `gemini` CLI to Vertex AI instead, edit
`~/.gemini/settings.json`:

```json
{
  "security": { "auth": { "selectedType": "vertex-ai" } }
}
```

Or run `gemini` interactively and pick “Vertex AI” from the auth menu.

## Credentials

Needs, in this order:
- `METAPROC_GCP_PROJECT` (already in `.env`) — used as `GOOGLE_CLOUD_PROJECT`.
- `~/.config/gcloud/application_default_credentials.json` from
  `gcloud auth application-default login`, OR a service-account key path in
  `GOOGLE_APPLICATION_CREDENTIALS`.

## Usage

```bash
uv run metaproc run-process process/self-test/smoke-adapter-gemini.process.md
```
