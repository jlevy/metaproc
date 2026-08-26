---
type: is
id: is-01m0zq5ddrhh3fwsbwj8cyday4
title: "PR49 CI: normalize styled Typer error output"
kind: bug
status: closed
priority: 1
version: 2
labels:
  - ci
  - test
dependencies: []
parent_id: is-01m0zp9smj0e1k0ng20wc27mj2
created_at: 2026-08-26T19:01:17.357Z
updated_at: 2026-08-26T19:05:56.571Z
closed_at: 2026-08-26T19:05:56.553Z
close_reason: PR 49 head 14c7e192 fails closed before shipping or dispatch when default Filestore lacks a server, preserves explicit --no-filestore execution, documents the behavior, and passes full local verification plus all five GitHub CI jobs. The cross-version styled-output assertion was normalized with click.unstyle.
resolution: null
duplicate_of: null
---
Python 3.14 CI renders the two hyphens in --no-filestore with Rich style boundaries, so the raw ANSI-bearing output substring assertion fails although the command correctly exits 2 before dispatch. Normalize with the already imported click.unstyle before asserting the actionable error text.
