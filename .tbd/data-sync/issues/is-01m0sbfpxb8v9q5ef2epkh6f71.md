---
type: is
id: is-01m0sbfpxb8v9q5ef2epkh6f71
title: "PR #30 review R4: Fail gcp logs for an unresolved exact Batch resource"
kind: bug
status: closed
priority: 1
version: 2
labels:
  - pr-review
dependencies: []
parent_id: is-01m0sbeq4f6ac3ayz46z7kc03h
created_at: 2026-08-24T07:41:45.258Z
updated_at: 2026-08-24T08:11:43.573Z
closed_at: 2026-08-24T08:11:43.573Z
close_reason: "Fixed in 1cbe8e8; Metaproc PR #30 canonical five-job CI is green."
resolution: null
duplicate_of: null
---
PR #30 review R4 (Medium), src/metaproc/commands/gcp.py:198-199,1213-1217. Exact user-named resources must surface lookup errors and exit nonzero, matching gcp status. Review: https://github.com/jlevy/metaproc/pull/30#issuecomment-5392026692
