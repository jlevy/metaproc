---
type: is
id: is-01m1fk1h4jj1t6x799snfqy533
title: Update cryptography past GHSA-g6cj-pr64-35w5
kind: bug
status: closed
priority: 0
version: 2
labels:
  - supply-chain
  - security
dependencies: []
created_at: 2026-09-01T22:57:06.705Z
updated_at: 2026-09-01T22:58:58.374Z
closed_at: 2026-09-01T22:58:58.374Z
close_reason: The Metaproc lock already contains cryptography 50.0.0 and its supply-chain record says no waiver is active. The apparent audit failure came from uv treating vendor/metaproc as a member of the parent Trading workspace and auditing Trading's 49.0.0 lock. No Metaproc dependency change is required; run the upstream gate from a standalone checkout.
resolution: canceled
duplicate_of: null
---
Metaproc make verify passes lint/types/docs/browser checks and 4,587 tests, then uv audit rejects locked cryptography 49.0.0 for GHSA-g6cj-pr64-35w5 / PYSEC-2026-3552. Fixed in 50.0.0. Update only through make lock after checking compatibility and supply-chain policy; rerun the full gate. Do not suppress the advisory.
