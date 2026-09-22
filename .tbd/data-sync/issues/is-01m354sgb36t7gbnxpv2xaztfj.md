---
type: is
id: is-01m354sgb36t7gbnxpv2xaztfj
title: "PR #87 review R4: collected-input digest and comparison logic belongs in engine/, not the command module"
kind: bug
status: closed
priority: 2
version: 3
labels: []
dependencies: []
parent_id: is-01m354sdgzcdwabh7t9rkzcmjj
created_at: 2026-09-22T18:07:03.011Z
updated_at: 2026-09-22T18:59:54.968Z
closed_at: 2026-09-22T18:59:54.968Z
close_reason: "Fixed in 95a6ada on PR #93"
resolution: null
duplicate_of: null
---
Medium. run_process.py _CollectedDocument, _collected_documents, _read_collected_inputs, _record_collected_inputs, comparison in _maybe_cascade_for_collected_inputs. Move to engine/collected_inputs.py; keep the cascade call and progress line in the command. PR #87
