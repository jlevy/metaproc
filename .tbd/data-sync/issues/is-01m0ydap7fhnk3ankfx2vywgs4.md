---
type: is
id: is-01m0ydap7fhnk3ankfx2vywgs4
title: Fail fast when standalone verification runs inside an enclosing uv workspace
kind: bug
status: open
priority: 3
version: 1
labels:
  - tooling
  - developer-experience
dependencies: []
created_at: 2026-08-26T06:50:10.031Z
updated_at: 2026-08-26T06:50:10.031Z
---
A nested checkout can cause uv to select the enclosing workspace, lock, and environment even when the child project is passed explicitly. Add a generic fail-fast standalone-workspace check only if it can name the discovered workspace and direct consumers to run integration checks from their own root. Do not change dependency policy or make standalone verification silently validate a consumer lock.
