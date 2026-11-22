---
type: is
id: is-01m76jstg5q3zxg920ww5a1sg5
title: Item-aligned chain members skip the fingerprint check on resume
kind: bug
status: open
priority: 2
version: 2
labels: []
dependencies:
  - type: blocks
    target: is-01m76jt5bwfqa7zkze6vva62tj
parent_id: is-01m76a30djgz1eds7sfd07wq3b
created_at: 2026-11-12T00:33:55.204Z
updated_at: 2026-11-12T00:34:06.330Z
---
Found by review A of PR #121 (finding A2, Medium):
https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
Line references are at dee3914e5e001aace3abc728d81b0c6f859845e1; the defect predates PR #121.

The level walk skips absorbed members of an item-aligned chain before any fingerprint
check (`src/metaproc/commands/run_process.py:6030-6032`, `:6130-6140`), and the chain
executor's `_is_done` (`:2917-2924`) calls `_item_task_is_done`, which looks only at an
item's status and outputs. A definition change on a non-head member (`stage-b` in a chain
`stage-a -> stage-b`, `align: same_key`) is therefore ignored on resume: nothing is
invoked, and `metaproc status --steps` shows `stage-b  stale ... definition changed` on
every later run. An edit to the chain head re-runs both members.

Fix (pick one, from the review): when the walk reaches a chain head, run
`_maybe_cascade_for_fingerprint` for each member of the chain; or state the limit in
design §10.3 and the operator flag table. The per-task comparison in the B1 bead makes
members re-run per item, but the downstream cascade still needs this walk fix. Add a
run-level test with a two-member chain whose second member's definition changes.
