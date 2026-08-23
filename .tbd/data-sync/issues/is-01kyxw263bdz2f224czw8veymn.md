---
type: is
id: is-01kyxw263bdz2f224czw8veymn
title: Add PyPI attestations to the Metaproc release workflow
kind: task
status: open
priority: 2
version: 13
spec_path: TODO.md
labels:
  - release
  - supply-chain
dependencies: []
parent_id: is-01kzky2kj5g9f2rxfq0wp15q5j
deferred_until: 2026-08-13T00:00:00Z
created_at: 2026-08-01T05:17:01.930Z
updated_at: 2026-08-16T21:21:25.975Z
extensions:
  linear:
    id: 8a8381cc-cad0-4dcf-b18b-2a34a5e5e475
    linked_at: 2026-08-16T08:00:49.127Z
---
Metaproc 0.2.0 was published through OIDC trusted publishing, but uv publish does not generate PEP 740 attestations, so PyPI exposes no provenance objects for the wheel or sdist. Replace or augment the publish step with an attestation-producing path and verify both Integrity API objects. PyPI recommends pypa/gh-action-pypi-publish; the latest reviewed-safe older action v1.14.0 pins pyasn1 0.6.1, which is rejected by this repo vulnerability policy, while v1.14.2 was published 2026-07-29 and is still inside the 14-day cool-off. Do not bypass policy: revisit after 2026-08-12T19:31:27Z, review and SHA-pin the action (or a pypi-attestations alternative), then exercise it on the next release.
