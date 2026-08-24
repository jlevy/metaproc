---
type: is
id: is-01m0rn0fqga1vrpjdq4edzs7yz
title: Make generic GCP Batch jobs reattachable and preserve baked dependencies
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
created_at: 2026-08-24T01:08:57.696Z
updated_at: 2026-08-24T01:16:44.298Z
closed_at: 2026-08-24T01:16:44.281Z
close_reason: Fixed in d092991; exact-resource status/log lookup recovered the live failure, wheel reinstall now preserves baked dependencies, full suite and PR CI passed.
resolution: null
duplicate_of: null
---
Live GTIA V2 replay showed two coupled bootstrap/diagnostic gaps: gcp logs could not target the full resource emitted by gcp run, and shipped-wheel reinstall re-resolved dependencies under the image global cutoff instead of preserving the audited baked closure. Accept exact job resources for delayed log recovery and reinstall the verified wheel with --no-deps.
