---
type: is
id: is-01m2khvjc8g634s9de5r5qqwhe
title: "PR #79 review S4: scalar agent timeouts draw the full 12-retry budget (up to 13x timeout_s wall clock)"
kind: task
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m2khtp39hxxwknrtk7gzqpfs
created_at: 2026-09-15T22:09:02.342Z
updated_at: 2026-09-15T22:56:36.941Z
closed_at: 2026-09-15T22:56:36.937Z
close_reason: "Done in ae5e096: operator reference states the 13x timeout multiplier and the settings that lower it; no TIMEOUT cap, to keep fan-out timeout behavior unchanged."
resolution: null
duplicate_of: null
---
PR #79 (https://github.com/jlevy/metaproc/pull/79#issuecomment-5688716379), Suggestion. Scalar timeouts now use RetryPolicy.max_retries (default 12) with the same backoff as nonzero exits, so a deterministically slow step costs up to 13x timeout_s. Either add a TIMEOUT cap in max_retries_for (as INVALID_OUTPUT has) or state the multiplier in the operator reference.
