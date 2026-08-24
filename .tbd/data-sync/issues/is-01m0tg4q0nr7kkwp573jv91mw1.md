---
type: is
id: is-01m0tg4q0nr7kkwp573jv91mw1
title: Make standalone verification reproducible across supported uv versions
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-08-24T18:22:22.228Z
updated_at: 2026-08-24T18:22:22.228Z
---
On 2026-08-24, standalone make verify stopped at uv sync --locked under uv 0.12.4 (and reviewed 0.11.26): uv reports addition of exclude-newer span P14D even though uv.lock already records exclude-newer-span = P14D. The lock was intentionally not rewritten. Determine whether the repository must pin its developer uv binary, regenerate with a compatible canonical lock workflow, or adjust relative-cutoff configuration so make install/verify is reproducible without lock mutation. All frozen post-install verification components and CI remain green.
