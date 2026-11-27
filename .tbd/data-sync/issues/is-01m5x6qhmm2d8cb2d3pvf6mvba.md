---
type: is
id: is-01m5x6qhmm2d8cb2d3pvf6mvba
title: "PR 117 A1-A4: one causal-outcome authority for run reports"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
created_at: 2026-10-26T22:54:05.971Z
updated_at: 2026-10-27T00:03:41.660Z
closed_at: 2026-10-26T23:42:03.268Z
close_reason: "Fixed on PR #117 at 90e0bb6: .state/finalization-outcome.json as the single causal-outcome authority (written only by a launch that observed its outcome), owner-aware finalization lock with ownership-safe release and an in-process handoff for signal-exit work, and Metabrowser plus resource-report recovery routed through infer_causal_outcome. Three further defects found in this round's own work were fixed in it: a recovery-written record made its inference permanent (15e2855), the lock used a different liveness convention from the orchestrator lease (66183d8), and lock reclaim followed a symlinked lock path and spun on a failed reclaim (90e0bb6). Regressions in tests/test_finalization_outcome.py, test_finalization_lock.py, test_mkdir_lock.py, test_metabrowser_api_resources.py and test_signal_exit_resource_summary.py. make verify green: 5718 passed, 15 skipped. PR left open for the owner to land."
resolution: null
duplicate_of: null
---
Senior review A on PR #117 found four High findings sharing one root cause: resource-usage-summary.md served both as a signal's durable outcome record and as status's optimistic publication token, and the publish lock did not exclude.

Fixed by recording the causal outcome on its own in .state/finalization-outcome.json (written first in every publication, replaced only by the run's own launches, read by every recovery path), by making the finalization lock owner-aware (PID+host inside the lock dir, reclaim only when the holder is known gone, ownership-safe release, in-process handoff for signal-exit work), and by routing Metabrowser and resource-report recovery through the same causal-outcome selection.

Regressions: tests/test_finalization_outcome.py, tests/test_finalization_lock.py, tests/test_metabrowser_api_resources.py, tests/test_signal_exit_resource_summary.py.

## Notes

Final head 19b3f2d, not 90e0bb6. A second independent review round found two more High defects in this round's own work: a resume killed by SIGKILL inherited the earlier launch's recorded outcome, and removing a lock followed a path swapped for a symlink and could remove a lock another holder had just acquired. Both fixed with probe-backed regressions in tests/test_finalization_outcome.py and tests/test_mkdir_lock.py. make verify green on 19b3f2d: 5722 passed, 15 skipped; CI green on all four jobs. PR #117 left open and unmerged for the owner to land.
