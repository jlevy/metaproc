---
type: is
id: is-01m2khvj1243rb3tp36amzwkah
title: "PR #79 review S3: engine/command_diagnostics imports underscore-private helpers from runtime.diagnostics"
kind: task
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m2khtp39hxxwknrtk7gzqpfs
created_at: 2026-09-15T22:09:01.985Z
updated_at: 2026-09-15T22:56:35.160Z
closed_at: 2026-09-15T22:56:35.156Z
close_reason: "Done in ec0bb11: core moved to engine/command_diagnostics with public names; runtime.diagnostics.summarize_diagnostic wraps it."
resolution: null
duplicate_of: null
---
PR #79 (https://github.com/jlevy/metaproc/pull/79#issuecomment-5688716379), Suggestion. src/metaproc/engine/command_diagnostics.py:9-13 imports _clip_diagnostic, _normalize_diagnostic, _redact_diagnostic from metaproc.runtime.diagnostics, inverting the rule in runtime/__init__.py that runtime is the public surface and engine private. Expose by name, or move the core into engine and let summarize_diagnostic wrap it.
