---
type: is
id: is-01m82beyvkx5hang2z1kt6zr4j
title: Publish Metaproc and verify registry artifacts
kind: task
status: closed
priority: 1
version: 4
labels: []
dependencies:
  - type: blocks
    target: is-01m82bf1hbjw1fqc25r1qe2p5s
parent_id: is-01m82be4yjpzsaestvwb057vxx
created_at: 2026-11-22T19:24:23.282Z
updated_at: 2026-11-22T20:50:30.553Z
closed_at: 2026-11-22T20:50:30.547Z
close_reason: Published v0.7.0 from f6697c9 in trusted publish run 45068171958. PyPI metadata and both files verified. Registry-backed isolated smoke passed for version, help, Agent Skill, and environment template.
resolution: null
duplicate_of: null
---
Publish the approved tag through GitHub trusted publishing, watch the workflow, inspect PyPI metadata, and smoke test the installed wheel.
