---
type: is
id: is-01m0v08wy0cem0nwa7zeejr8qd
title: "PR #35 lifecycle correctness follow-ups before live smoke"
kind: task
status: in_progress
priority: 1
version: 9
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
child_order_hints:
  - is-01m0vhr6mdh086pqvkbj7ysevv
  - is-01m0vhr77daaz65d7aeb0jd9n7
  - is-01m0vhr7jzzdqr6nmzp9dv22dt
  - is-01m0vhr7xyfyp95pz2xhsxd513
  - is-01m0vhr8b8gkgykcywwcq4z55c
created_at: 2026-08-24T23:04:16.575Z
updated_at: 2026-08-25T05:51:52.132Z
---
Parent ledger for the round-2 lifecycle findings. Children mp-kxmn, mp-e9e5, mp-d50w, and mp-0xbi are correctness gates before live smoke: bound descendant bookkeeping, catch late descendants, avoid cancelled-state poisoning, and prevent kill-sentinel retry churn. Child mp-bq47 is explicitly deferred: failed-item resume is sufficient now, while targeted rerun of a successful mapped item requires operator evidence.

## Notes

ADD from #37 head 49064f0: commit 49064f0 defers qualified per-item force. Rationale is sound for FAILED items (ordinary resume re-enters a failed mapped item without rerunning siblings; pinned by test_run_process.py:1706-1727) — this corrects my earlier F6 framing. Residual gap: no supported way to redo a SUCCESSFUL item (run-wide --force reruns all N; manual state edits explicitly unsupported). Ask: make the escalation trigger name that case concretely ('re-run a completed mapped item without re-running siblings') so it is testable.
