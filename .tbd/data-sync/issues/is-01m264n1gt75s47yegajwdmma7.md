---
type: is
id: is-01m264n1gt75s47yegajwdmma7
title: "Model review follow-up: migrate retiring Vertex MaaS models"
kind: task
status: open
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels: []
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
due_date: 2026-10-01T16:00:00Z
created_at: 2026-09-10T17:08:09.369Z
updated_at: 2026-10-10T09:06:59.668Z
---
Google Cloud announces 2026-10-21 retirement for all six Vertex MaaS IDs packaged by Metaproc: zai-org/glm-5-maas, zai-org/glm-4.7-maas, deepseek-ai/deepseek-v3.2-maas, moonshotai/kimi-k2-thinking-maas, qwen/qwen3-235b-a22b-instruct-2507-maas, and qwen/qwen3-coder-480b-a35b-instruct-maas. The 2026-10-01 review deadline has passed. Identify affected profiles and consumer configuration, select managed or self-deployed replacements, test model identity and tool calls on intended routes, document the migration and historical-ID retention policy, and complete the transition before retirement. Existing IDs remain accepted only for compatibility until the decision ships. Source: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/deprecations/open-models

## Notes

2026-10-10 review confirmed that the existing bead omitted the two packaged Qwen routes even though the official retirement table includes them. All six committed Vertex MaaS entries retire in 11 days. No replacement or live probe was selected during this read-only review.
