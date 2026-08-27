---
type: is
id: is-01m10c2865xxchw79knf65gaet
title: "PR #49 R2: Make declared Gemini inputs natively readable in ignored trees"
kind: bug
status: closed
priority: 1
version: 4
labels: []
dependencies: []
parent_id: is-01m10c27jjs2qh7hbcn3msz564
created_at: 2026-08-27T01:06:33.796Z
updated_at: 2026-08-27T01:54:10.879Z
closed_at: 2026-08-27T01:54:10.879Z
close_reason: null
resolution: null
duplicate_of: null
---
Use invocation-scoped Gemini settings so a declared input under an ignored runtime tree can be read by the native file tool in an untrusted workspace. Do not modify interactive or user-global settings. Add adapter serialization and subprocess boundary coverage.

## Notes

Fixed in the current PR49 worktree with invocation-scoped Gemini native settings that disable ignore filtering only for the launched process. Adapter serialization and full framework verification pass; awaiting pushed commit, CI, and consumer rerun.
