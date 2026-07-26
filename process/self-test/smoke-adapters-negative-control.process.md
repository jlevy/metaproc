---
process:
  name: smoke-adapters-negative-control
  description: >-
    Negative-control smoke: every adapter must REJECT an obviously bogus
    model name. Pairs with `smoke-adapters-all`, which verifies the
    positive path (model matches expected). Each step here inverts the
    expected exit code with `bash -lc '! <cmd>'`, so the step is green
    only when the inner command fails.

    This exists because all four adapters silently rewrite unknown
    `--model` values at the metaproc level (claude, gemini, codex,
    pi all log.warning and fall back to the default — and codex-cli
    itself also silently falls back at the CLI level, so there's no
    external validation either). `--assert-model` is the only check
    that catches this; the negative control verifies it still fires.

    Every step uses `metaproc auth-check --live --variant
    <adapter>-bogus-xyz-nomatch --assert-model bogus-xyz-nomatch`:
    the adapter silently rewrites the model to its default, the live
    probe subprocess exits 0, but `--assert-model` sees the observed
    default instead of `bogus-xyz-nomatch` and fails the check. For
    pi, the pre-flight `_pi_validate_registration` rejects the
    unregistered model even before dispatch.

  steps:
    - id: claude-bogus-model-rejected
      mode: code
      command: >-
        bash -lc "unset METAPROC_GCP_SECRET_CLAUDE_CREDS && cd ../../.. && ! uv run metaproc auth-check --live --variant claude-code-cli-bogus-xyz-nomatch --assert-model bogus-xyz-nomatch"
      description: >-
        claude-code-cli silently rewrites unknown `--model` back to
        CLAUDE_DEFAULT_MODEL (see `_build_claude_flags`). The live
        probe subprocess returns 0, but the `system.init` event carries
        the real default model — `--assert-model bogus-xyz-nomatch` sees
        no match and fails the check. `!` inverts: step green iff
        auth-check exits non-zero.

    - id: gemini-bogus-model-rejected
      mode: code
      command: >-
        bash -lc "export GOOGLE_GENAI_USE_VERTEXAI=true && export GOOGLE_CLOUD_PROJECT=\"$METAPROC_GCP_PROJECT\" && export GOOGLE_CLOUD_LOCATION=\"${GOOGLE_CLOUD_LOCATION:-global}\" && cd ../../.. && ! uv run metaproc auth-check --live --variant gemini-cli-bogus-xyz-nomatch --assert-model bogus-xyz-nomatch"
      description: >-
        Same shape as claude: gemini-cli silently rewrites unknown
        `--model` to GEMINI_DEFAULT_MODEL, the subprocess exits 0, and
        `--assert-model` catches the observed/expected mismatch.

    - id: pi-bogus-model-rejected
      mode: code
      command: >-
        bash -lc "cd ../../.. && ! uv run metaproc auth-check --live --variant pi-cli-bogus-xyz-nomatch --provider vertex-maas --assert-model bogus-xyz-nomatch"
      description: >-
        pi-cli has a stronger guarantee than claude/gemini: the
        `_pi_validate_registration` pre-flight in auth-check refuses to
        dispatch when the model is not registered with pi's
        `--list-models`. Live probe exits non-zero before any prompt
        is sent.

    - id: codex-bogus-model-rejected
      mode: code
      command: >-
        bash -lc "cd ../../.. && ! uv run metaproc auth-check --live --variant codex-cli-bogus-xyz-nomatch --assert-model bogus-xyz-nomatch"
      description: >-
        Same shape as claude/gemini: the metaproc codex adapter
        silently rewrites unknown `--model` back to CODEX_DEFAULT_MODEL
        (see `_build_codex_flag_groups`), codex dispatches with the
        default, and its untyped config-dump preamble carries
        `{"model": <default>}`. `--assert-model bogus-xyz-nomatch`
        sees "gpt-5.4" (or current default) — no substring match — and
        fails the check. `!` inverts: step green iff auth-check exits
        non-zero. Confirms `--assert-model`'s new codex support works
        even though codex-cli itself does NOT validate unknown model
        names (both the adapter and the CLI silently fall back).
---
# smoke-adapters-negative-control — bogus model must red every adapter

Pairs with `smoke-adapters-all` to close a blind spot: three of the four adapters
silently rewrite unknown `--model` values back to their `*_DEFAULT_MODEL` constant, and
codex-cli’s JSONL stream does not carry a model ID. Without a negative control, the
positive-path smoke can go green even if the model-assertion or pre-flight registration
logic is disabled or regresses.

## What each step proves

| Adapter | Claim it verifies |
| --- | --- |
| `claude-code-cli` | Adapter silently falls back on unknown model → `--assert-model` catches the mismatch. |
| `gemini-cli` | Same as claude. |
| `pi-cli` | `_pi_validate_registration` pre-flight refuses to dispatch on unregistered model. |
| `codex-cli` | Same as claude/gemini. codex-cli itself does NOT reject unknown `-m <model>` (silently falls back too), so `--assert-model` is the only line of defence; this step confirms it still catches the mismatch for codex via the config-dump preamble. |

## Why `bash -lc '! <cmd>'`?

`metaproc run-process` treats a non-zero step exit as failure.
For a negative control, the inner command is EXPECTED to fail — so we invert the exit
with `!` and the step is green only when the inner command failed.
No engine change needed (no `expect-fail` step mode).

## Usage

```bash
uv run metaproc run-process process/self-test/smoke-adapters-negative-control.process.md
```

## When this is red

A red step here means its adapter is NOT catching bogus models — either `--assert-model`
regressed (claude/gemini/codex) or the pi pre-flight was bypassed.
Fix the corresponding validation path; do not relax this smoke.

## Scope

- Every step round-trips a trivial prompt through the real backend (because
  `metaproc auth-check --live` always dispatches before the assertion fires), so total
  token cost is ~4 prompts.
  Wall clock ≈ sum of four live-probe round-trips (~40 s sequential; less in parallel).
- Not part of `smoke-adapters-all` because the positive and negative paths have inverted
  exit-code semantics and bundling them would confuse red/green interpretation.
