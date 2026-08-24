---
type: is
id: is-01m0t7zzb7md36w7w0jnadbcts
title: "PR #35 review 1: preserve credential scrubbing in LocalBackend"
kind: bug
status: open
priority: 0
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d44v9sfzcegwcth6e1b4
created_at: 2026-08-24T15:59:58.311Z
updated_at: 2026-08-24T16:38:13.000Z
---
Review https://github.com/jlevy/metaproc/pull/35#issuecomment-5397585319. LocalBackend.launch merges os.environ beneath prepared.env, reintroducing keys deliberately removed by compose_slot_env and routing pooled work to ambient credentials. Treat prepared.env as complete or carry explicit unsets; add a real child absence test covering scalar and fan-out.

## Notes

PR #34 e3f177b now proves ambient credential absence in the real scalar child. PR #35 must still fix LocalBackend's env merge and prove absence in the real backend/fan-out child path.
