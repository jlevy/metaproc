---
type: is
id: is-01m4nngf18peg02yza5s8j15sq
title: "PR #105 C2: env_vars.py says 'When truthy' for GOOGLE_GENAI_USE_VERTEXAI; only exactly true is honoured"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m4nngebtw2grshwdvpd3g8np
created_at: 2026-10-11T14:22:45.287Z
updated_at: 2026-10-11T14:37:37.460Z
closed_at: 2026-10-11T14:37:37.459Z
close_reason: "Fixed in be56b5a: GOOGLE_GENAI_USE_VERTEXAI registry text says exactly true; registry test fails before. Reply https://github.com/jlevy/metaproc/pull/105#issuecomment-6110135184"
resolution: null
duplicate_of: null
---
Low. https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5483638299. config/env_vars.py:200-202 vs check_auth gemini_cli.py:849 and both CLIs (=== 'true').
