---
type: is
id: is-01m4myb8pkzafqtr0tjr10pmqr
title: "PR #106 B5: 200-char truncation in extract_log_error can flip a hard-cap refusal to retry"
kind: bug
status: open
priority: 2
version: 4
delegate: claude-code@spud10
labels: []
dependencies: []
parent_id: is-01m4my9r60j9s9nkm8t1rnam6f
hold: null
hold_until: null
created_at: 2026-10-11T07:37:57.714Z
updated_at: 2026-10-11T14:27:39.970Z
started_at: 2026-10-11T13:13:56.027Z
---
Low. Review B https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482373521. retry.py:639 returns str(value)[:200]; _is_rate_quota needs the hard marker absent, so a truncated tail hides daily wording. Fix: classify on the untruncated terminal message.

## Notes

Deferred from PR #106 (review B5). The mapped code path already classifies the full handler diagnostic and clips only the stored message (handler_failure_message). The agent paths instead build one exit_error from extract_log_error's 200-character slice and use it for classification, the status record, the progress line, and the auth classifier, so a hard-cap marker past the cut can be lost. Splitting classification text from display text in both agent paths changes that shared error plumbing and is outside PR #106. Until then the misread is bounded, though not to the item alone: a hard cap misread as a per-minute refusal retries at most MAX_QUOTA_RETRIES_DEFAULT (3) times, and because it also sets rate_quota it cuts the pool's provider ceiling. Under a cap that refuses every call, each failure from a process admitted after the last cut cuts again and every refusal extends the recovery hold, so the ceiling can fall to min_concurrency for the whole pool, not only for the item (review C6, https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5483637641). Waits on: a decision on extract_log_error's contract (return the full terminal message and clip at the status and display boundary).
