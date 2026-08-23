---
type: is
id: is-01m0nrc2gk81258hat3v2v2vw1
title: "PR #26 review L5: only ----delimited frontmatter handled, silently"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0nrb7r4e9ejhv5yp3q4amm0
created_at: 2026-08-22T22:09:59.827Z
updated_at: 2026-08-22T22:32:56.855Z
closed_at: 2026-08-22T22:32:56.855Z
close_reason: null
---
schema_conform.py:266 gates on startswith('---\n'); the HTML-comment style is skipped indistinguishably from 'nothing to fix', while fmf_read_frontmatter_artifact reads every style.
