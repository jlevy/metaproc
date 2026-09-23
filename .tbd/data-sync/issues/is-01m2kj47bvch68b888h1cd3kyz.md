---
type: is
id: is-01m2kj47bvch68b888h1cd3kyz
title: "PR #83 review P2-R4: --step-variant is not persisted for resume or status"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:45.978Z
updated_at: 2026-09-16T00:17:12.678Z
closed_at: 2026-09-16T00:17:12.675Z
close_reason: "Fixed in 27dcf17: run-config.yaml records step_variants; resume re-applies them, a different set is refused, status plans them. Test: test_a_resume_reapplies_recorded_step_variants_and_refuses_a_different_set."
resolution: null
duplicate_of: null
---
PR #83 part 2 R4 (Low). _write_run_config does not record step overrides; resume without the flag re-plans on the run profile; status --steps misreports. run_process.py:756-846; status.py:83-90; operator reference 547-552.
