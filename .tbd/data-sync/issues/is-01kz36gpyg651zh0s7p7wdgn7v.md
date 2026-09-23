---
type: is
id: is-01kz36gpyg651zh0s7p7wdgn7v
title: "PR #9 review PR9-R6: make typed-prefix registration atomic"
kind: bug
status: closed
priority: 3
version: 3
labels:
  - pr-review
  - pr-9
dependencies: []
parent_id: is-01kz36g3q9wbmhwnwcs170y1s3
created_at: 2026-08-03T06:55:55.855Z
updated_at: 2026-08-03T07:05:28.475Z
closed_at: 2026-08-03T07:05:28.474Z
close_reason: "Fixed: prefix registration now rejects bare strings, materializes and validates the complete batch, then updates the global registry atomically. Failure leaves no partial prefix; both regression tests pass."
---
Formal review PR9-R6 (Low), PR #9. src/metaproc/ids.py:82. register_typed_id_prefixes mutates the global registry before the entire batch validates. Materialize and validate first, reject bare strings, update atomically, and prove failed calls leave no partial registration.
