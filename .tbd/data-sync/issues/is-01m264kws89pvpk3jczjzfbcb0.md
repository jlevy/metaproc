---
type: is
id: is-01m264kws89pvpk3jczjzfbcb0
title: "Model review follow-up: upgrade pinned clients for current Claude models"
kind: task
status: open
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
due_date: 2026-10-31T00:00:00.000Z
created_at: 2026-09-10T17:07:31.751Z
updated_at: 2026-10-10T09:06:49.917Z
---
The repository pins Claude Code 2.1.234 and Pi 0.84.2. Official Claude Code documentation requires at least 2.1.257 for Fable 5.1, 2.1.280 for Opus 5.5, 2.1.284 for Sonnet 5.5, and 2.1.293 for Haiku 5.5. Coordinate pinned-client and deployment-image upgrades under the supply-chain policy; update the accepted exact IDs and alias/lifecycle notes; preserve version checks; and verify model identity and representative tool calls on intended routes. Add Pi-native acceptance only after its pinned catalog supports each exact ID. Catalog inclusion must remain separate from account and live-route evidence.

## Notes

2026-10-10 monthly review: current Anthropic lineup is claude-fable-5-1, claude-opus-5-5, claude-sonnet-5-5, and claude-haiku-5-5. Opus 5.5 released 2026-09-22, Sonnet 5.5 on 2026-09-28, and Haiku 5.5 on 2026-10-07. Sources: https://platform.claude.com/docs/en/models/overview ; https://code.claude.com/docs/en/model-config ; https://platform.claude.com/docs/en/about-claude/model-deprecations . Claude Sonnet 4.5 retires 2026-11-30, although its exact deprecated ID is not currently in Metaproc native_models. No live inference was performed.
