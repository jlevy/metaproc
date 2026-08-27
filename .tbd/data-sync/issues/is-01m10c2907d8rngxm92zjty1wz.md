---
type: is
id: is-01m10c2907d8rngxm92zjty1wz
title: "PR #49 R5: Make Gemini tool declarations truthful"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m10c27jjs2qh7hbcn3msz564
created_at: 2026-08-27T01:06:34.630Z
updated_at: 2026-08-27T01:06:34.630Z
---
The Gemini adapter accepts a declared tools policy without enforcing it in the launched CLI. Either translate the policy through a supported Gemini mechanism or reject/omit the unsupported declaration so process specifications do not imply confinement that does not exist.
