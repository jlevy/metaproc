---
type: is
id: is-01m1fyd3s3210trwmw10gkse2j
title: Make Metaproc verification independent of a consumer uv workspace
kind: bug
status: open
priority: 2
version: 1
labels:
  - build
  - uv
dependencies: []
created_at: 2026-09-02T02:15:40.578Z
updated_at: 2026-09-02T02:15:40.578Z
---
Running make format or make verify from a Metaproc checkout nested as trading's vendor/metaproc submodule makes uv discover the parent trading workspace and reuse its .venv. After the Metabrowser 0.9.1 upgrade, sync resolves the consumer's metabrowser-plugin-dataroom==0.1.0 against Metaproc[browser]==0.9.1 and fails before any Metaproc gate runs. Reproduce from the submodule, then make the repository Make targets select a standalone project/environment without weakening locked resolution or the supply-chain cutoff. CI's standalone clone is unaffected.
