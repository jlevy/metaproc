---
type: is
id: is-01m354scsdvpeftedmv2gphtbz
title: "PR #86 review R4: run-config.yaml should show the values the resume ran with"
kind: bug
status: closed
priority: 3
version: 3
labels: []
dependencies: []
parent_id: is-01m354s9wem4g14nhcnb7z1gvw
created_at: 2026-09-22T18:06:59.373Z
updated_at: 2026-09-22T18:59:57.044Z
closed_at: 2026-09-22T18:59:57.044Z
close_reason: "variables and step variants rewritten (95a6ada, PR #93); run_dir kept at creation value because the results projection rebases from it"
resolution: null
duplicate_of: null
---
Low. After a change the current value lives only in a JSONL event. Rewrite run-config.yaml fields to the values the resume ran with, after recording the change. PR #86
