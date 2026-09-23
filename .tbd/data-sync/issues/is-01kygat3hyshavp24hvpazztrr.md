---
type: is
id: is-01kygat3hyshavp24hvpazztrr
title: Pin Metaproc as the downstream Git submodule
kind: task
status: closed
priority: 1
version: 7
spec_path: docs/project/specs/done/plan-2026-07-26-standalone-extraction.md
labels: []
dependencies: []
parent_id: is-01kygat035xcheze599f3yxqrb
created_at: 2026-07-26T23:05:23.773Z
updated_at: 2026-08-09T18:57:01.774Z
closed_at: 2026-07-27T16:26:31.003Z
close_reason: A downstream consumer branch pins standalone commit a89af85c058eebe85b4978df707a52b1cd828b2f; local integration gates pass and the consumer PR is ready once private-submodule checkout credentials are configured.
---
Replace the former in-tree package with a submodule pinned to the exact validated standalone commit and verify downstream integration.
