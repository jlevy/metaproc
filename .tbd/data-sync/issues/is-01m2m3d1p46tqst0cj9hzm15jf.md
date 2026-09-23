---
type: is
id: is-01m2m3d1p46tqst0cj9hzm15jf
title: "PR #83 re-review N2: a v0.4.1 run launched with --step-variant cannot be resumed with the same flag"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m2m3cqjhtn554qyf84g883r9
created_at: 2026-09-16T03:15:40.867Z
updated_at: 2026-09-16T03:32:25.035Z
closed_at: 2026-09-16T03:32:25.032Z
close_reason: "Fixed in 70c812c: _resume_step_variants adopts the requested overrides when the config records none, and _write_run_config records them through _adopt_step_variants after launch validation. A recorded set still refuses a different one. Regression test test_a_resume_adopts_the_step_variants_a_run_recorded_none_of fails at 316f496 with the 'records no --step-variant' CLIError."
resolution: null
duplicate_of: null
---
--step-variant shipped in v0.4.1 without being recorded in run-config.yaml. Under _resume_step_variants (src/metaproc/commands/run_process.py:1172-1193) such a run refuses a resume that repeats the same flag ('records no --step-variant but you launched with ...'), and the workaround of dropping the flag silently re-plans the step on the run profile, which is the defect P2-R4 fixed.

Fix: when the config records no overrides and the resume passes some, adopt and record them on that resume; keep refusing a genuine mismatch, meaning a different recorded set. Recording must happen after launch validation so an invalid override is not persisted.

Regression tests: an unrecorded run adopting the flag, and a recorded run still refusing a different set.
