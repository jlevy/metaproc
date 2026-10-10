---
type: is
id: is-01m4jh3cv4ngys53482cm684md
title: "October model review: migrate Codex default and add current GPT-6 IDs"
kind: task
status: open
priority: 1
version: 1
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels:
  - model-catalog
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
due_date: 2026-10-13T00:00:00.000Z
created_at: 2026-10-10T09:07:59.448Z
updated_at: 2026-10-10T09:07:59.448Z
---
Metaproc defaults codex-cli to gpt-5.5, which official OpenAI documentation says retires from Codex with ChatGPT sign-in on 2026-10-14. The current catalog accepts gpt-6-astra and older GPT-5.6 IDs but omits current gpt-6-sol, gpt-6.1-sol, and gpt-6-luna. Decide defaults separately by authentication route; add exact current IDs and lifecycle notes; retain still-valid API-only historical selections explicitly; remove the already-retired gpt-5.3-codex-spark from current Codex choices or mark it historical; update Pi Responses overrides and pricing where supported; and verify the pinned Codex 0.147.0 command path preserves each chosen model and effort. Do not infer ChatGPT-plan access from API documentation or change a default without migration evidence. Official sources: https://learn.chatgpt.com/docs/models ; https://developers.openai.com/api/docs/models ; https://developers.openai.com/api/docs/deprecations . No live inference was performed in the 2026-10-10 review.
