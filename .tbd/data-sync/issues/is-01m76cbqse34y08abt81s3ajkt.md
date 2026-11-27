---
type: is
id: is-01m76cbqse34y08abt81s3ajkt
title: "PR #122 A3: PR body cites a CI failure on main that no main run shows"
kind: bug
status: closed
priority: 3
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76cbg8653gb7qwb22zsqfgn
hold: null
hold_until: null
created_at: 2026-11-11T22:41:22.221Z
updated_at: 2026-11-11T22:43:37.958Z
started_at: 2026-11-11T22:41:30.559Z
closed_at: 2026-11-11T22:43:37.957Z
close_reason: "Fixed by editing the PR #122 body: make audit fails against main's lock; main's last CI run (40414976612, 2026-10-26) passed and the advisories (published 2026-10-30) surfaced in PR #121's lint job."
resolution: null
duplicate_of: null
---
PR #122 review A, finding A3 (Low): https://github.com/jlevy/metaproc/pull/122#pullrequestreview-5865991209

The PR body's Summary says main's CI lint job fails at make audit. The last CI run on main (run 40414976612 at 4289836, 2026-10-26) passed; the four advisories were published 2026-10-30 and surfaced in PR #121's lint job (run 43071302105).

Fix: reword the PR body so it says make audit fails against main's lock, as seen in #121's lint job.
