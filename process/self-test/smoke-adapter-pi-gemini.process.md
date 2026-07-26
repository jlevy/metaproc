---
process:
  name: smoke-adapter-pi-gemini
  description: >-
    Per-adapter smoke for pi-cli routed through google-vertex against a
    Gemini model. Mirrors `smoke-adapter-gemini` (which exercises the
    gemini-cli harness) but uses pi-cli as the harness. Required because
    pi-cli's google-vertex API path has its own quirks — most notably the
    Gemini-3 `thought_signature` requirement on tool calls — that the
    gemini-cli smoke does not exercise. See
    `docs/runbooks/adapter-compatibility.runbook.md` §1 for the 2026 r5
    400-storm that motivated this smoke.

    Defaults to `gemini-3.1-pro-preview` (registered in
    `pi-models.default.json`); override per cell via
    `PI_GEMINI_SMOKE_MODEL`. Uses GCP Application Default Credentials —
    no API key needed.

  steps:
    - id: binary-check
      mode: code
      command: >-
        bash -lc "pi --version"
      description: Confirm the pi-cli binary is installed and on PATH.

    - id: auth-check-dry
      mode: code
      command: >-
        bash -lc "cd ../../.. && uv run metaproc auth-check --variant pi-cli"
      description: >-
        Dry survey — binary path + credential source (`~/.pi/auth.json`)
        + GCP ADC for google-vertex.
      needs: [binary-check]

    - id: live-probe
      mode: code
      command: >-
        bash -lc "cd ../../.. && SMOKE_MODEL=\"${PI_GEMINI_SMOKE_MODEL:-gemini-3.1-pro-preview}\" && SMOKE_ASSERT=\"${PI_GEMINI_SMOKE_ASSERT_MODEL:-$SMOKE_MODEL}\" && uv run metaproc auth-check --live --variant \"pi-cli-$SMOKE_MODEL\" --provider google-vertex --assert-model \"$SMOKE_ASSERT\""
      description: >-
        Trivial "Respond with exactly: OK" prompt through pi-cli with
        `--provider google-vertex` and the requested Gemini model.
        `_pi_validate_registration` pre-flight rejects the model if it
        is not in pi's `--list-models`, so this also confirms the
        operator's pi installation has the requested model registered.
        `--assert-model` (substring) catches silent provider fallback.
      needs: [auth-check-dry]

    - id: tool-probe
      mode: code
      command: >-
        bash -lc "cd ../../.. && SMOKE_MODEL=\"${PI_GEMINI_SMOKE_MODEL:-gemini-3.1-pro-preview}\" && uv run metaproc probe-tool-use --harness pi-cli --provider google-vertex --model \"$SMOKE_MODEL\""
      description: >-
        Round-trip a single file-read tool call. Verifies both that pi
        emits a `toolcall_end` event AND that the model echoes the
        controlled sentinel string in its response. This is the test
        that catches the Gemini-3 `thought_signature` regression class
        — a model that text-generates fine but cannot complete a tool
        call will fail here even when `live-probe` is green.
      needs: [live-probe]
---
# smoke-adapter-pi-gemini — live smoke for pi-cli + Gemini (google-vertex)

Mirror of `smoke-adapter-gemini` for the pi-cli harness.
Confirms that pi-cli’s native `google-vertex` API path round-trips a Gemini model
end-to-end including tool use.

## Why a separate smoke

The existing `smoke-adapter-pi` exercises `vertex-maas/glm-5-maas`, which is a different
API (Vertex MaaS, openai-completions-shaped).
The google-vertex path used for Gemini has its own auth flow and its own tool-call
contract — most critically the Gemini-3 `thought_signature` requirement that produced
the 2026 r5 400-storm on the openai-completions path
([adapter-compatibility runbook §1](../../docs/runbooks/adapter-compatibility.runbook.md)).

## Steps

1. **binary-check** — `pi --version`.
2. **auth-check-dry** — `metaproc auth-check --variant pi-cli` (covers GCP ADC for
   google-vertex by virtue of the gcloud-token check).
3. **live-probe** —
   `metaproc auth-check --live --variant pi-cli-<model> --provider google-vertex --assert-model <model>`.
   Defaults to `gemini-3.1-pro-preview`; override with `PI_GEMINI_SMOKE_MODEL`.
4. **tool-probe** —
   `metaproc probe-tool-use --harness pi-cli --provider google-vertex --model <model>`.
   Issues one file-read and verifies the sentinel-string round-trip.

## Per-model overrides

Used by `smoke-gemini-matrix` to exercise three Gemini models on this harness:

```bash
PI_GEMINI_SMOKE_MODEL=gemini-3.5-flash \
  uv run metaproc run-process process/self-test/smoke-adapter-pi-gemini.process.md
```

If Vertex returns an observed model ID that differs from the requested one (e.g.
`models/gemini-3.5-flash-001`), `--assert-model` does substring matching so the bare ID
is still tight; override `PI_GEMINI_SMOKE_ASSERT_MODEL` only if you need a different
substring.

## Credentials

- `~/.pi/auth.json` from `pi auth`.
- GCP Application Default Credentials (`gcloud auth application-default login`) so the
  google-vertex API path can pick up a token.
- `METAPROC_GCP_PROJECT` from `.env`.

No `GEMINI_API_KEY` or `GOOGLE_API_KEY` required — the google-vertex path uses ADC.

## Usage

```bash
# Default model (gemini-3.1-pro-preview)
uv run metaproc run-process process/self-test/smoke-adapter-pi-gemini.process.md

# Specific model
PI_GEMINI_SMOKE_MODEL=gemini-3.5-flash \
  uv run metaproc run-process process/self-test/smoke-adapter-pi-gemini.process.md
```
