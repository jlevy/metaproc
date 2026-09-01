---
type: is
id: is-01m1d6825bap227nnhj4db8c4w
title: Honor configured Gemini working directories
kind: bug
status: closed
priority: 0
version: 3
labels:
  - adapters
  - isolation
dependencies: []
created_at: 2026-09-01T00:35:00.384Z
updated_at: 2026-09-01T00:47:49.342Z
closed_at: 2026-09-01T00:47:49.341Z
close_reason: "Fixed by PR #58: Gemini adapter now honors configured working_directory with full verify and hosted CI green."
resolution: null
duplicate_of: null
---
GeminiCliAdapter rejects and ignores working_directory even though every launch path passes adapter.working_directory() into the subprocess. A downstream production cohort demonstrated that inheriting the repository root causes cross-item discovery and severe memory/token amplification. Accept the public config key, return its Path when set, preserve the absent-key behavior, and cover both validation and resolution.
