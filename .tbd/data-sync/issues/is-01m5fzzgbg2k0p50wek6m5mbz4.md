---
type: is
id: is-01m5fzzgbg2k0p50wek6m5mbz4
title: v0.5.0 release
kind: task
status: in_progress
priority: 1
version: 2
labels: []
dependencies: []
created_at: 2026-10-21T19:45:59.152Z
updated_at: 2026-10-21T19:51:58.345Z
---
Release v0.5.0: a minor, because it raises the Python floor to 3.14 (stdlib compression.zstd).

Records: CHANGELOG [0.5.0], docs/project/releases/v0.5.0.md, docs/publishing.md step 5. Compatibility leads with the 3.14 floor, the unverified GCP image interpreter (mp-mo74), Metabrowser not reading .zst, and mapped agent steps on profiles with different pool resources now failing in one local run.

Done when v0.5.0 is tagged from main, publish.yml succeeds, PyPI metadata is checked, the docs/publishing.md step-9 smoke tests pass against the released version, and TODO.md § Current Release names v0.5.0.
