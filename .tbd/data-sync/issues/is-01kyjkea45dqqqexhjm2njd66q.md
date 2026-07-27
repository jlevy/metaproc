---
type: is
id: is-01kyjkea45dqqqexhjm2njd66q
title: Handle Git history scan timeouts as hygiene findings
kind: bug
status: closed
priority: 3
version: 2
spec_path: docs/project/specs/active/plan-2026-07-26-standalone-extraction.md
labels: []
dependencies: []
parent_id: is-01kyje203wwq9b8jqxgwe7574v
created_at: 2026-07-27T20:14:43.332Z
updated_at: 2026-07-27T20:19:40.317Z
closed_at: 2026-07-27T20:19:40.316Z
close_reason: Fixed in 00d0f14 with focused timeout-path coverage. Lint, public-hygiene, distribution, Cursor review, and Python 3.12/3.13/3.14 hosted checks pass.
---
The reachable-history scanner must convert subprocess timeout failures into explicit hygiene findings instead of raising and aborting the lint command. Add focused error-path coverage.
