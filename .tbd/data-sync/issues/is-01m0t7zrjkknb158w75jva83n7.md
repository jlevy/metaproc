---
type: is
id: is-01m0t7zrjkknb158w75jva83n7
title: "PR #33 review R8: surface invalid max concurrency as CLIError"
kind: bug
status: open
priority: 2
version: 1
labels:
  - pr-review
dependencies: []
parent_id: is-01m0t5d3edn704qec0gz9asyve
created_at: 2026-08-24T15:59:51.379Z
updated_at: 2026-08-24T15:59:51.379Z
---
Review https://github.com/jlevy/metaproc/pull/33#issuecomment-5397584816. RunExecutionContext.create raises bare ValueError for max_concurrency < 1, so an invalid default environment value produces a traceback. Validate at the CLI boundary and raise CLIError.
