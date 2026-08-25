---
type: is
id: is-01m0v08vwjm25wnjd15f5e5xd7
title: "PR #37 I8: anchor item keys against . and ..; add containment test"
kind: bug
status: closed
priority: 1
version: 5
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - pr-review
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:15.505Z
updated_at: 2026-08-25T19:28:30.034Z
closed_at: 2026-08-25T13:19:02.279Z
close_reason: "Re-verified addressed at PR #37 head b5c4721: scope identity/containment and exception/concurrency fixes are present, required-edge graph work is split into the base, and the branch descends from post-#38 main without restoring workstation aliasing. Full and pre-push verification passed."
resolution: null
duplicate_of: null
---
ITEM_KEY_RE fullmatches '.' and '..' (verified), so a roster row keyed '..' builds child_run_dir = run_dir and writes a run tree into the parent run root; state dir name degrades to 'tasks'. Anchor the regex/validator to reject dot-only keys and add the plan-mandated path-containment test. Review: pull/37 comment (B4); holistic ledger #8.

## Notes

Re-verified OPEN at #37 head 49064f0: paths.py:19 ITEM_KEY_RE unchanged, '.' and '..' still fullmatch.
