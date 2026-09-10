---
type: is
id: is-01m260jstxtek1v68pmkdd521m
title: "PR 75 F1: retain diagnostics across typed RunPool event reads"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T15:57:01.659Z
updated_at: 2026-09-10T15:57:01.659Z
---
Typed readers drop controller fields from concurrency_adjust/pressure_check and skip quota_pause_started/tick/resumed plus health_sample. Add current writer fields and event models without changing existing JSON, with writer-to-reader regression coverage.
