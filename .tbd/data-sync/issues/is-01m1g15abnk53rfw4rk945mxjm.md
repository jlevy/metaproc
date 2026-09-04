---
type: is
id: is-01m1g15abnk53rfw4rk945mxjm
title: "PR62 review F2: nonforking owned launch has no Python design"
kind: bug
status: closed
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies: []
parent_id: is-01m1g159htcr2kbsgb0mnzkyx2
created_at: 2026-09-02T03:03:50.901Z
updated_at: 2026-09-02T03:16:40.434Z
closed_at: 2026-09-02T03:16:40.433Z
close_reason: Fixed in 1333fd5 on codex/runpool-host-safety-plan (pull request 62); disposition recorded in the review addendum.
resolution: null
duplicate_of: null
---
CPython subprocess uses posix_spawn only without start_new_session/close_fds (subprocess.py:1825-1839); backend.py:270 uses start_new_session=True. Add Launch Primitive section (os.posix_spawn setsid, exit waiter via pidfd/kqueue, wrapper registration handshake, sampling replaces memory_pressure.py) and a spike bead before mp-3c0g. (review F-id in title; PR 62; plan files under docs/project/specs/active/)
