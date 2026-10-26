---
type: is
id: is-01m5x5d4yy46gsrkknxhy6qpvc
title: Publish the v0.6.0 release
kind: task
status: open
priority: 1
version: 1
labels: []
dependencies: []
created_at: 2026-10-26T22:30:56.733Z
updated_at: 2026-10-26T22:30:56.733Z
---
Release v0.6.0 from main.

Delta since v0.5.0 (36c8099 at prep time): the gcp run file console and its live view, Softschema 0.9 validation execution evidence, the frontmatter-format 0.5 reader requirement, the mapped-composite scope ceiling following the run ceiling, and three fixes that were live in v0.5.0 (guarded frontmatter repair, durable terminal status timestamps, pure-emitter safe YAML writers).

A minor, not a patch: the file console adds CLI surface (gcp run --console/--watch-run, gcp logs --errors/--follow/--cloud-logging, gcp status --run-status), the softschema and frontmatter-format floors rise, and the mapped-composite scope ceiling changes concurrency for an existing process.

Done when v0.6.0 is tagged from main, publish.yml succeeds, PyPI metadata is checked, the docs/publishing.md step-9 smoke tests pass against the released version, and TODO.md Current Release names v0.6.0.
