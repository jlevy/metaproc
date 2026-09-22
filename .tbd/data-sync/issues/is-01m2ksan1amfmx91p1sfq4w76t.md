---
type: is
id: is-01m2ksan1amfmx91p1sfq4w76t
title: Carry --step-variant into run-process --cloud dispatch, or refuse it
kind: bug
status: open
priority: 3
version: 1
labels: []
dependencies: []
created_at: 2026-09-16T00:19:36.617Z
updated_at: 2026-09-16T00:19:36.617Z
---
Found while addressing the PR #83 review (part 2, R4). 'run-process --cloud' builds OrchestratorDispatchConfig in commands/run_process.py without the parsed step_profile_overrides, so a launch that passes --step-variant submits an orchestrator that plans every step on the run profile, with no warning. Either thread the overrides through OrchestratorDispatchConfig and the orchestrator command line (they are now recorded in run-config.yaml, so a resume on the VM would reuse them), or refuse --cloud together with --step-variant at launch validation. Pre-existing; not introduced by #83.
