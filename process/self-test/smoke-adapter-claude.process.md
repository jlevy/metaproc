---
process:
  name: smoke-adapter-claude
  description: >-
    Per-adapter smoke for claude-code-cli. Confirms the binary is on PATH,
    the local credential source (macOS Keychain OAuth or
    `ANTHROPIC_API_KEY`) is detectable, and a trivial prompt round-trips
    through the real Anthropic backend. Scoped to LOCAL dispatch only —
    the Secret Manager cloud-dispatch credential (Phase 2b) is covered by
    a separate check, so a stale cloud cred does not red this smoke when
    the local path is fine.

  steps:
    - id: binary-check
      mode: code
      command: >-
        bash -lc "claude --version"
      description: Confirm the claude-code-cli binary is installed and on PATH.

    - id: auth-check-dry
      mode: code
      command: >-
        bash -lc "unset METAPROC_GCP_SECRET_CLAUDE_CREDS && cd ../../.. && uv run metaproc auth-check --variant claude-code-cli"
      description: >-
        Dry survey — binary path + local credential source. Explicitly
        unsets `METAPROC_GCP_SECRET_CLAUDE_CREDS` so Phase 2b does not
        auto-fire from the operator's shell env; the Secret Manager
        probe is a separate cloud-cred concern.
      needs: [binary-check]

    - id: live-probe
      mode: code
      command: >-
        bash -lc "unset METAPROC_GCP_SECRET_CLAUDE_CREDS && cd ../../.. && uv run metaproc auth-check --live --variant claude-code-cli --assert-model opus"
      description: >-
        Send a trivial "Respond with exactly: OK" prompt via `claude -p`
        and assert the `model` field in the stream-json `system.init`
        event contains "opus" (the CLAUDE_DEFAULT_MODEL alias, which the
        CLI expands to `claude-opus-4-X`). Catches silent fallback if
        `claude --model opus` were ever ignored.
      needs: [auth-check-dry]
---
# smoke-adapter-claude — live smoke for claude-code-cli

Three-step gate for claude-code-cli: binary → local credential → live prompt.
Serial; total wall clock ≈ 10–15 seconds.

## Steps

1. **binary-check** — `claude --version`.
2. **auth-check-dry** — `metaproc auth-check --variant claude-code-cli`.
3. **live-probe** —
   `metaproc auth-check --live --variant claude-code-cli --assert-model opus` (forces
   `output_format: stream-json` so the `system.init` event carrying `model` lands in
   stdout).

Scope: LOCAL Claude dispatch only.
The Phase 2b Secret Manager probe is unset from the environment explicitly so that a
stale cloud credential does not red this smoke — that is a separate cloud-dispatch
concern.

## Credentials

One of:

- macOS Keychain OAuth (`claudeAiOauth` keychain entry, populated by `claude auth` or
  Claude Desktop).
- `ANTHROPIC_API_KEY` environment variable.

## Cloud-dispatch credential (separate check)

To explicitly probe the cloud-dispatch credential stored in GCP Secret Manager, run
auth-check directly with the ref:

```bash
uv run metaproc auth-check --live --variant claude-code-cli \
  --claude-secret-ref projects/PROJECT/secrets/NAME/versions/latest
```

Or set `METAPROC_GCP_SECRET_CLAUDE_CREDS` in the shell before running `auth-check`
without a variant — Phase 2b auto-triggers for the no-variant and
`--variant claude-code-cli` combinations.

## Usage

```bash
uv run metaproc run-process process/self-test/smoke-adapter-claude.process.md
```
