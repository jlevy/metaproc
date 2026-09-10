---
type: is
id: is-01m0r9d1zvx40602kj7egnnqas
title: Stage task outputs per attempt and publish accepted commits
kind: feature
status: open
priority: 1
version: 4
spec_path: src/metaproc/docs/execution-model-design.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0r9d2ckfh04hjx3v2qgafy7
  - type: blocks
    target: is-01m260z10t7p5h7maqpws2x4rc
parent_id: is-01m0r93gwcj17mn4dmw1ts7fqa
created_at: 2026-08-23T21:46:06.714Z
updated_at: 2026-09-10T16:03:42.234Z
---
Resolve declared outputs into attempt-private staging, validate the complete set there, and publish only after a fenced commit is accepted. Keep artifact paths portable and leave rejected or superseded attempts available for inspection without making them visible to downstream tasks.
