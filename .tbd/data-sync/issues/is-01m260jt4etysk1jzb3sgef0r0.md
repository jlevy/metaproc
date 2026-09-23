---
type: is
id: is-01m260jt4etysk1jzb3sgef0r0
title: "PR 75 F2: clean up launches when post-spawn bookkeeping fails"
kind: bug
status: closed
priority: 1
version: 6
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T15:57:01.965Z
updated_at: 2026-09-10T18:47:00.036Z
closed_at: 2026-09-10T16:23:20.351Z
close_reason: Review and bounded fixes complete; full design review published on PR 75 with per-finding dispositions. Six lifecycle regressions reproduced failures before repair; make verify passed (4622 passed, 8 skipped), including audits and distribution smoke. Commit/push/CI remain tracked by mp-5v99.
resolution: null
duplicate_of: null
---
RunPool._launch_and_monitor records child identity, start event, and status before entering its cleanup try block. A record_child or startup telemetry failure leaves a launched child unowned while _run_process releases host/pool admission. Protect post-spawn setup with the same cleanup ownership as polling and preserve the primary cause; inject failures in regression tests.

## Notes

Six lifecycle regressions reproduced each original failure and pass after repair. Includes quota timer cancellation/await and no timer creation after shutdown, child reaping before host release, unreturned-lease cleanup, capacity reuse despite log I/O failures, and primary error preservation. Full make verify passed: 4622 passed, 8 skipped, including locked audits and installed-wheel smoke. Full review: https://github.com/jlevy/metaproc/pull/75#issuecomment-5621778931
