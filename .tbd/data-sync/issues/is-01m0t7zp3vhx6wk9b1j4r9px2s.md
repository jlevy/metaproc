---
type: is
id: is-01m0t7zp3vhx6wk9b1j4r9px2s
title: "PR #33 review R3: make execution-context close nonblocking"
kind: bug
status: open
priority: 1
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:48.858Z
updated_at: 2026-08-24T15:59:48.858Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. RunExecutionContext.close calls ThreadPoolExecutor.shutdown(wait=True) from the coroutine finally path and can block the event loop for an unbounded running subprocess. Use a nonblocking or bounded off-loop shutdown while preserving lifecycle ownership.
