---
type: is
id: is-01m355rqtnpw1nmfegrqt3grq4
title: "PR 92 R2: preserve diagnostic copy permissions and metadata"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m355h6et8q600r0pv9c30k49
created_at: 2026-09-22T18:24:06.482Z
updated_at: 2026-09-22T18:24:06.482Z
---
PR 92 review R2: dispatch/slot_coordinator.py:565 replaces copy2 with copyfile_atomic, widening 0600 to 0644. Stage copy2 with atomic_output_file and regression-test modes, timestamps, failed copy.
