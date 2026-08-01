---
type: is
id: is-01kyxw263bdz2f224czw8veymn
title: Add PyPI attestations to the Metaproc release workflow
kind: task
status: open
priority: 2
version: 1
spec_path: docs/releases/v0.2.0.md
labels:
  - release
  - supply-chain
dependencies: []
parent_id: is-01kyx37mj1agq5zha1x5gn574f
deferred_until: 2026-08-13T00:00:00Z
created_at: 2026-08-01T05:17:01.930Z
updated_at: 2026-08-01T05:17:01.930Z
---
Metaproc 0.2.0 was published through OIDC trusted publishing, but uv publish does not generate PEP 740 attestations, so PyPI exposes no provenance objects for the wheel or sdist. Replace or augment the publish step with an attestation-producing path and verify both Integrity API objects. PyPI recommends pypa/gh-action-pypi-publish; the latest reviewed-safe older action v1.14.0 pins pyasn1 0.6.1, which is rejected by this repo vulnerability policy, while v1.14.2 was published 2026-07-29 and is still inside the 14-day cool-off. Do not bypass policy: revisit after 2026-08-12T19:31:27Z, review and SHA-pin the action (or a pypi-attestations alternative), then exercise it on the next release.
