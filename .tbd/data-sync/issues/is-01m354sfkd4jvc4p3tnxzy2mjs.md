---
type: is
id: is-01m354sfkd4jvc4p3tnxzy2mjs
title: "PR #87 review R3: byte sha256 in collected-inputs.yaml is a hash check nobody verifies"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01m354sdgzcdwabh7t9rkzcmjj
created_at: 2026-09-22T18:07:02.253Z
updated_at: 2026-09-22T18:59:54.296Z
closed_at: 2026-09-22T18:59:54.295Z
close_reason: "Fixed in 95a6ada on PR #93"
resolution: null
duplicate_of: null
---
Medium, ceremony. models/runtime.py CollectedInput.sha256, engine/fan_in.py OutcomeManifest.sha256. Drop; keep outcomes_sha256. PR #87
