---
type: is
id: is-01m0nxdjckk0pd9qa4b2qm3901
title: "PR #27 review R1: TestWhichExecutorsRepair does not assert code-branch scoping"
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m0nxcy12vxk88gp9w80cs400
created_at: 2026-08-22T23:38:11.731Z
updated_at: 2026-08-22T23:52:42.345Z
closed_at: 2026-08-22T23:52:42.345Z
close_reason: "Replaced TestWhichExecutorsRepair with TestWhichExecutorsRewriteAgentOutput: a behavioral mode:code run plus a positional parse-tree check. Verified against all four regressions in 4bf776b."
---
tests/test_yaml_repair.py:146-181. Injecting repair_declared_outputs() into the mode:code branch at run_parallel.py:1034 leaves both tests green. The class docstring and docs/arch/arch-metaproc-core.md:2000 claim the scoping is asserted; only the indirection is. Fix: add a behavioral test over the mode:code fan-out path.
