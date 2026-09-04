---
type: is
id: is-01m1q4eqvrk6tzqwvjtwktchhd
title: User-level uv config leaks first-party exemptions into uv.lock
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-09-04T21:16:06.391Z
updated_at: 2026-09-04T21:16:06.391Z
---
Any resolving uv command in this repo rewrites uv.lock's [options.exclude-newer-package] table with ~15 first-party entries dated 2100-01-01, sourced from the developer's user-level ~/.config/uv/uv.toml. The repo's own uv.toml declares only 5 package-scoped exemptions.

Two concrete harms observed while working on PR #68:
1. The mutated lockfile is stageable and would silently commit personal supply-chain exemptions that then apply to CI and every other contributor, contradicting the 14-day cool-off policy in SUPPLY-CHAIN-SECURITY.md.
2. It makes 'make verify' order-dependent: with a clean lockfile 'uv sync --locked' fails ('lockfile needs to be updated'), but it passes if an earlier command already mutated the file. Observed both outcomes in one session.

Passing --config-file does not isolate it; uv still merges the user-level config.

Fix options: a pre-commit or verify guard that fails when uv.lock's exclude-newer-package table does not match uv.toml, and/or run repo uv commands with user config suppressed.
