---
type: is
id: is-01kyx385z9ab0pdr0aajs4c32e
title: Bump softschema constraint to >=0.3.0,<0.4 and relock
kind: chore
status: open
priority: 1
version: 2
labels:
  - softschema
dependencies:
  - type: blocks
    target: is-01kyx38gn4gwmp93rst4psbm0x
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-07-31T22:03:24.008Z
updated_at: 2026-07-31T22:03:34.947Z
---
Constraint was >=0.1.4,<0.2. softschema 0.3.0 published 2026-07-12, so it already clears the repo's 14-day cool-off; no uv.toml exclude-newer-package exception is needed. Note: 'make lock' must be run from a standalone checkout - inside the trading superproject uv resolves against the parent workspace and fails.
