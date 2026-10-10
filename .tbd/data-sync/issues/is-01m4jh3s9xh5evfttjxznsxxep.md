---
type: is
id: is-01m4jh3s9xh5evfttjxznsxxep
title: "October model review: reconcile Gemini lifecycle, profiles, and packaged metadata"
kind: task
status: open
priority: 1
version: 1
spec_path: docs/project/specs/active/plan-2026-09-10-model-catalog-followups.md
labels:
  - model-catalog
dependencies: []
parent_id: is-01m26342tjmj8qh22599bhd3sn
due_date: 2026-11-01T00:00:00.000Z
created_at: 2026-10-10T09:08:12.217Z
updated_at: 2026-10-10T09:08:12.217Z
---
Google API release notes deprecated gemini-3.5-flash and gemini-3.7-flash on 2026-10-08 and auto-route them to gemini-3.6-flash and gemini-3.8-flash, while Vertex publishes separate lifecycle dates. Record the surface-specific lifecycle instead of treating one route as universal; review execution profiles and smoke examples that still request 3.5 Flash; plan the gemini-3.6-flash Vertex migration before its 2026-11-19 retirement; and keep valid historical IDs only with explicit compatibility notes. Correct the packaged gemini-3.1-pro-preview-customtools context window from 2,097,152 if primary evidence continues to specify 1,048,576, and verify the pinned Gemini CLI 0.59.0 dynamic-resolution path preserves 3.8 without claiming live compatibility. Sources: https://ai.google.dev/gemini-api/docs/changelog ; https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions ; https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-pro . The current catalog already includes 3.8 Flash and 3.5 Flash-Lite; this bead is lifecycle, metadata, profile, and route reconciliation, not a duplicate addition. No live inference was performed.
