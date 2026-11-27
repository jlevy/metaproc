---
type: is
id: is-01m4mydayp5z79t1tvntdy52ae
title: "PR #105 B3: GOOGLE_GENAI_USE_GCA selects a login the private home cannot hold; check_auth reports ready"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10
labels: []
dependencies: []
parent_id: is-01m4mybyqptgk7zm6660wmwtzb
hold: null
hold_until: null
created_at: 2026-10-11T07:39:05.544Z
updated_at: 2026-10-11T13:51:12.915Z
started_at: 2026-10-11T13:32:02.452Z
closed_at: 2026-10-11T13:51:12.915Z
close_reason: "Fixed in e85f952 (check_auth follows CLI env order; prepare_env refuses GCA without login). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722673"
resolution: null
duplicate_of: null
---
Medium, introduced. Review B https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482362930. gemini-cli getAuthTypeFromEnv order is GCA, Vertex, GOOGLE_GEMINI_BASE_URL, GEMINI_API_KEY; adapters/gemini_cli.py:601-655 knows only API key then Vertex. Under template-less user scope GCA has no cached login. Fix (pick one): prepare_env raises for GCA without GOOGLE_CLOUD_ACCESS_TOKEN / GOOGLE_APPLICATION_CREDENTIALS, or check_auth follows the CLI order; add GOOGLE_GENAI_USE_GCA and GOOGLE_GEMINI_BASE_URL to the runbook and MetaprocEnv.
