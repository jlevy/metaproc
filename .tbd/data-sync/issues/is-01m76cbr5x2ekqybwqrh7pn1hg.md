---
type: is
id: is-01m76cbr5x2ekqybwqrh7pn1hg
title: "PR #122 B3: record the artifact-to-tag check that stands in for provenance"
kind: task
status: closed
priority: 3
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76cbg8653gb7qwb22zsqfgn
hold: null
hold_until: null
created_at: 2026-11-11T22:41:22.620Z
updated_at: 2026-11-11T22:43:37.641Z
started_at: 2026-11-11T22:41:30.938Z
closed_at: 2026-11-11T22:43:37.640Z
close_reason: "Fixed in f16a100: the pyasn1 entry records the comparison with unsigned upstream tag v0.6.5 (commit 5d16736) that stands in for provenance."
resolution: null
duplicate_of: null
---
PR #122 review B, suggestion B3: https://github.com/jlevy/metaproc/pull/122#pullrequestreview-5866218020

The A1 paragraph records that PyPI has no provenance bundle for pyasn1 0.6.5. Add one sentence to SUPPLY-CHAIN-SECURITY.md saying what stood in for provenance: both artifacts were compared with the unsigned upstream tag v0.6.5 (commit 5d16736).
