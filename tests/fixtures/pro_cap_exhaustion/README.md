# pro_cap_exhaustion fixtures

Sanitized session-log + stderr captures from Claude Code CLI invocations that hit
either:

- **monthly-cap/**: Anthropic Console plan monthly org cap exhausted.
- **pro-5h-cap/**: Pro plan 5-hour rolling cap exhausted.

Each subdirectory contains:

- `session.jsonl` — per-attempt JSONL with `result` block and accumulated `usage`
  blocks. Redact `request_id` and any user-identifying fields.
- `stderr.txt` — captured stderr text.
  May be empty.
- `metadata.yaml` — `cap_type | date | cli_version | label | notes`.

Background: [authentication architecture](../../../src/metaproc/docs/arch-authentication.md).

Tracking bead: the fix (5.5a, research).

These fixtures unblock the fix (5.5b, classifier implementation): a regression
test asserts each fixture → expected `KnownBugSignature.name` + `classification`.

## Status

- [ ] **monthly-cap fixture** — message text confirmed by a sanitized operator report:
  `"You've hit your org's monthly usage limit"`. Operator capture of a sanitized JSONL
  still outstanding.
- [ ] **pro-5h-cap fixture** — message text **unconfirmed**. The empirical gap.
  Record the CLI version and sanitized JSONL when an operator catches this in the wild.
