---
type: is
id: is-01m0y91krfa247y1t49yaa9aem
title: Restore locked install and make verify with relative exclude-newer
kind: bug
status: closed
priority: 1
version: 3
labels:
  - tooling
  - supply-chain
dependencies: []
created_at: 2026-08-26T05:35:18.279Z
updated_at: 2026-08-26T06:50:11.038Z
closed_at: 2026-08-26T06:50:11.037Z
close_reason: "Rebutted after standalone reproduction: Metaproc locked install and make verify pass with the P14D relative window. The failure came from uv selecting an enclosing consumer workspace; generic fail-fast UX is tracked separately."
resolution: null
duplicate_of: null
---
On current main with uv 0.12.4, uv --config-file uv.toml sync --all-extras --all-groups --locked reports that the checked-in uv.lock needs an update because of the P14D exclude-newer span, even though the lock records exclude-newer-span = P14D. This blocks make install, format, and verify before project checks run. Diagnose the uv relative-cutoff/lock interaction and restore a reproducible locked install without weakening the supply-chain window. Keep this separate from feature changes.

## Notes

The locked-install failure also prevents the standard no-build-isolation distribution build from finding hatchling in the project environment. Direct lint and tests can run with uv run --frozen, but make verify cannot be claimed until locked install is restored.
