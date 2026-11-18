---
type: is
id: is-01m7pabd42n430ps81tfe9wfhf
title: "Issue 85: write usage summaries on signal exit via cooperative TERM and resume backfill"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
created_at: 2026-11-18T03:14:05.057Z
updated_at: 2026-11-18T03:14:05.057Z
---
GitHub issue jlevy/metaproc#85. A gpt-6.2-astra read-only analysis on 2026-11-18 (against main 5ea6732 and PR 117 head 19b3f2d) confirms the root cause: run-process's hard reaper re-raises SIGTERM under SIG_DFL so the finally that calls finalize_run_resources never runs, and status recovery skips active (resumed) runs. It recommends replacing PR 117 with a focused change: (1) route SIGTERM through cooperative cancellation of the root task so the existing finalizers run, with a watchdog armed before dispatch and the descendant reaper as bounded fallback; (2) repair the previous launch's reports at resume acceptance (_RunConfigWrite.resumed) before new work changes the evidence; (3) make report publication conditional on a launch identity derived from the lease owner token, shared by status, resource-report, Metabrowser refresh and operations-summary writes, preserving the live ledger inode. PR 117 findings: heavy work inside the signal handler before the 8 s bound, cross-launch signal attribution after TERM-then-KILL, active recovery mixing launches and replacing the live ledger, and a residual mkdir-lock reclaim race. Port PR 117's real-signal subprocess fixtures. Ship separately from the v0.7.0 agent-CLI and model-catalog upgrade.
