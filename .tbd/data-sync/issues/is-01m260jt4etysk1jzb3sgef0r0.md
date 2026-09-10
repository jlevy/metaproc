---
type: is
id: is-01m260jt4etysk1jzb3sgef0r0
title: "PR 75 F2: clean up launches when post-spawn bookkeeping fails"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T15:57:01.965Z
updated_at: 2026-09-10T15:57:01.965Z
---
RunPool._launch_and_monitor records child identity, start event, and status before entering its cleanup try block. A record_child or startup telemetry failure leaves a launched child unowned while _run_process releases host/pool admission. Protect post-spawn setup with the same cleanup ownership as polling and preserve the primary cause; inject failures in regression tests.
