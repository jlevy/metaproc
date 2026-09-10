---
type: is
id: is-01m2639apah4xf43xqrzh6g0wa
title: "PR 69: verify OpenAI model IDs and Codex availability"
kind: task
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
created_at: 2026-09-10T16:44:16.969Z
updated_at: 2026-09-10T17:42:10.432Z
closed_at: 2026-09-10T17:42:10.431Z
close_reason: Official source and pinned-client review complete; catalog fixes integrated and remaining client/migration/live-evidence work tracked in separate sub-beads.
resolution: null
duplicate_of: null
---
Sol research: verify exact Codex/API model IDs, aliases, status, account-surface differences, and effort settings against fetched official documentation; check Pi routing separately.

## Notes

Sol research verified exact OpenAI API/Codex model IDs and alias/preview distinctions using official model pages. Rechecked the exact pinned Codex 0.147.0 source: eight named efforts through ultra parse; operational support remains model-specific. Verified Pi 0.84.2 per-model Responses override and inherited baseURL behavior; new entries use that route. Current selection, routing, and falsey-value regressions are included.
