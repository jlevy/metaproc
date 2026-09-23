---
type: is
id: is-01m35rfb4af0g8avqyacw66w33
title: Gemini run path refuses every result for catalog alias model ids
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-09-22T23:51:01.514Z
updated_at: 2026-09-22T23:51:01.514Z
---
From PR #94 review S4: validate_result_event compares the alias (flash, pro, flash-lite, auto, auto-gemini-3) against stats.models keyed by the resolved concrete id, so every success is refused. No shipped profile uses an alias. Options: resolve the alias first, drop aliases from the gemini catalog, or accept any served model for an alias request.
