---
type: is
id: is-01m0v08vwjm25wnjd15f5e5xd7
title: "PR #37 I8: anchor item keys against . and ..; add containment test"
kind: bug
status: open
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d345y4pdjcjpepb9h4q6
created_at: 2026-08-24T23:04:15.505Z
updated_at: 2026-08-24T23:14:29.581Z
---
ITEM_KEY_RE fullmatches '.' and '..' (verified), so a roster row keyed '..' builds child_run_dir = run_dir and writes a run tree into the parent run root; state dir name degrades to 'tasks'. Anchor the regex/validator to reject dot-only keys and add the plan-mandated path-containment test. Review: pull/37 comment (B4); holistic ledger #8.

## Notes

Re-verified OPEN at #37 head 49064f0: paths.py:19 ITEM_KEY_RE unchanged, '.' and '..' still fullmatch.
