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

Companion research brief:
[`docs/project/research/research-2026-05-11-claude-cli-cap-exhaustion-signals.md`](../../../../docs/project/research/research-2026-05-11-claude-cli-cap-exhaustion-signals.md)

Tracking bead: `internal-reference` (5.5a, research).

These fixtures unblock `internal-reference` (5.5b, classifier implementation): a regression
test asserts each fixture → expected `KnownBugSignature.name` + `classification`.

## Status

- [ ] **monthly-cap fixture** — message text confirmed in
  [run-report-2026-04-29 § Issue 4](../../../../docs/project/specs/done/run-report-2026-04-29-wednesday-dispatch.md#issue-4--anthropic-monthly-org-cap-hit-cost-17-stub-predictions--30-of-wasted-turns):
  `"You've hit your org's monthly usage limit"`. Operator capture of a sanitized JSONL
  still outstanding.
- [ ] **pro-5h-cap fixture** — message text **unconfirmed**. The empirical gap.
  See research brief § Open questions for what to record alongside the JSONL when an
  operator catches this in the wild.
