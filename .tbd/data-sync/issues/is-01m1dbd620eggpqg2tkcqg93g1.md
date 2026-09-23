---
type: is
id: is-01m1dbd620eggpqg2tkcqg93g1
title: Disposition stale in-progress tracking against terminal pull requests
kind: task
status: closed
priority: 1
version: 2
labels:
  - release-blocker
dependencies: []
parent_id: is-01m1dbcer80nak10tnbg1jyq52
created_at: 2026-09-01T02:05:11.103Z
updated_at: 2026-09-01T05:23:24.086Z
closed_at: 2026-09-01T05:23:24.084Z
close_reason: "Closed mp-zwih, mp-gg32, mp-5248, mp-bjrn, mp-srbl, mp-1af0, and the shipped epic mp-0iy8 against their terminal pull requests. mp-5igv was re-scoped rather than closed: its code half merged in PR #44, but the candidate-image probe, live canary, and provider credential rotation are live-environment facts no repository check can confirm. mp-flfr and mp-fvdg remain genuinely open developer-environment issues, not stale tracking."
resolution: null
duplicate_of: null
---
Ten beads are in_progress; six describe work whose pull request is already terminal, so
the board cannot be read as a release signal. Verified against GitHub at 2026-08-31:

- mp-5igv (P0) - PR 44 MERGED. Its own notes say "Commit, stacked PR, candidate-image
  probe, and successful live canary remain", all of which happened. A P0 security bead
  sitting open against merged work is the single most misleading item on the board.
- mp-srbl - merged; notes already record focused tests, full verification, exact-head
  public CI, and a live dispatch confirming job creation.
- mp-zwih - review of PR 37, which is CLOSED (superseded by the consolidated branch).
- mp-gg32 - review of PR 38, MERGED.
- mp-5248 - review of PR 39, MERGED.
- mp-bjrn - decision on PR 19, MERGED.

Also stale in content rather than status: mp-1af0 is in_progress and its notes describe
work "uncommitted in the working tree" that landed in PR 49.

Close each with a close reason naming the merge or closure that settled it, or state
plainly what genuinely remains and re-scope the bead to only that. Do not close by
assumption - mp-5igv in particular asserts live-environment evidence, so confirm the
canary and rotation actually completed before closing it, and split out anything that
did not.
