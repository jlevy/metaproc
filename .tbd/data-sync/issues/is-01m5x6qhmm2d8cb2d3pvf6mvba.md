---
type: is
id: is-01m5x6qhmm2d8cb2d3pvf6mvba
title: "PR 117 A1-A4: one causal-outcome authority for run reports"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
created_at: 2026-10-26T22:54:05.971Z
updated_at: 2026-10-26T22:54:05.971Z
---
Senior review A on PR #117 found four High findings sharing one root cause: resource-usage-summary.md served both as a signal's durable outcome record and as status's optimistic publication token, and the publish lock did not exclude.

Fixed by recording the causal outcome on its own in .state/finalization-outcome.json (written first in every publication, replaced only by the run's own launches, read by every recovery path), by making the finalization lock owner-aware (PID+host inside the lock dir, reclaim only when the holder is known gone, ownership-safe release, in-process handoff for signal-exit work), and by routing Metabrowser and resource-report recovery through the same causal-outcome selection.

Regressions: tests/test_finalization_outcome.py, tests/test_finalization_lock.py, tests/test_metabrowser_api_resources.py, tests/test_signal_exit_resource_summary.py.
