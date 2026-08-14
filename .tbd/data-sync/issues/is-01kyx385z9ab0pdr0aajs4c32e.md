---
type: is
id: is-01kyx385z9ab0pdr0aajs4c32e
title: Bump softschema constraint to >=0.3.0,<0.4 and relock
kind: chore
status: closed
priority: 1
version: 3
labels:
  - softschema
dependencies:
  - type: blocks
    target: is-01kyx38gn4gwmp93rst4psbm0x
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-07-31T22:03:24.008Z
updated_at: 2026-07-31T22:27:50.110Z
closed_at: 2026-07-31T22:27:50.109Z
close_reason: Bumped to >=0.3.0,<0.4 and relocked; 0.3.0 already clears the 14-day cool-off
---
Constraint was >=0.1.4,<0.2. softschema 0.3.0 published 2026-07-12, so it already clears the repo's 14-day cool-off; no uv.toml exclude-newer-package exception is needed. Note: 'make lock' must be run from a standalone checkout - inside the trading superproject uv resolves against the parent workspace and fails.
