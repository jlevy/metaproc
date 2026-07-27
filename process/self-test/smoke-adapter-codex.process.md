---
process:
  name: smoke-adapter-codex
  description: >-
    Per-adapter smoke for codex-cli. Confirms the binary is on PATH, the
    credential source is detectable, and a trivial prompt round-trips
    through the real OpenAI backend. This is the TDD red-green test for
    the auth-check harness fix — before the fix, `live-probe` raises
    `ValueError: codex-cli: need one of permission_mode ...` because
    `_run_live_check` passes `merged_config={}`.

  steps:
    - id: binary-check
      mode: code
      command: >-
        bash -lc "codex --version"
      description: Confirm the codex-cli binary is installed and on PATH.

    - id: auth-check-dry
      mode: code
      command: >-
        bash -lc "cd ../../.. && uv run metaproc auth-check --variant codex-cli"
      description: >-
        Dry survey — binary path + credential source (OPENAI_API_KEY or
        ChatGPT OAuth via ~/.codex/auth.json). Exits non-zero if either
        is missing.
      needs: [binary-check]

    - id: live-probe
      mode: code
      command: >-
        bash -lc "cd ../../.. && uv run metaproc auth-check --live --variant codex-cli --assert-model gpt"
      description: >-
        Send a trivial "Respond with exactly: OK" prompt via
        `codex exec --json` and assert the `model` field in codex-cli's
        untyped config-dump preamble line contains "gpt". The typed
        event stream (thread.started → turn.started → item.* →
        turn.completed) never carries a model ID — the preamble is the
        only place it appears. RED against auth_check.py prior to the
        permission-mode-default fix.
      needs: [auth-check-dry]
---
# smoke-adapter-codex — live smoke for codex-cli

Three-step gate for codex-cli: binary → credential → live prompt.
Serial because each step gates the next; total wall clock ≈ 10–15 seconds end-to-end.

## Steps

1. **binary-check** — `codex --version`. Fails fast if the CLI isn’t installed.
2. **auth-check-dry** — `metaproc auth-check --variant codex-cli`. Confirms the
   credential source is detectable without dispatching a prompt.
3. **live-probe** — `metaproc auth-check --live --variant codex-cli --assert-model gpt`.
   Dispatches the trivial “Respond with exactly: OK” prompt through the real OpenAI
   backend and asserts the `model` field in codex-cli’s untyped config-dump preamble
   line contains “gpt”.
   The typed event stream does not carry a model ID; the preamble is the only place it
   appears. Bogus-model negative control lives in
   `smoke-adapters-negative-control.process.md`.

## Credentials

One of:

- `OPENAI_API_KEY` environment variable, or
- `~/.codex/auth.json` from `codex login` with `cli_auth_credentials_store = "file"` in
  `~/.codex/config.toml`.

## Usage

```bash
uv run metaproc run-process process/self-test/smoke-adapter-codex.process.md
```

## Known-red condition

This process fails at `live-probe` on any commit prior to the `_run_live_check`
codex-permission-mode fix (the harness was built with a hard-coded claude-code-cli
branch and no codex branch, so `build_command` rejects the empty `merged_config`).
That’s the intended TDD loop: run this process first to confirm red, fix
`src/metaproc/commands/auth_check.py`, re-run to confirm green.
