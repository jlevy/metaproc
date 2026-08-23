---
type: is
id: is-01m0rfkbbhxd1j78wzxnzdnp3h
title: Make the rolling dependency cool-off compatible with locked verification
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-08-23T23:34:24.368Z
updated_at: 2026-08-23T23:34:24.368Z
---
On 2026-08-23, make verify fails in its install prerequisite because uv.toml uses a relative 14-day exclude-newer window and uv --locked re-resolves to newly eligible packages beyond the 2026-08-21 lock. The remaining frozen lint/type/test/audit/distribution checks pass. Define a stable verification/relock policy so an unchanged branch does not become unverifiable as the wall clock advances.
