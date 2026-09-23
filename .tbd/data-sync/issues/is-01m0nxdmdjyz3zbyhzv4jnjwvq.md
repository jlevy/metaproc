---
type: is
id: is-01m0nxdmdjyz3zbyhzv4jnjwvq
title: "PR #27 review R6: arch doc Pass 1 still names yaml.safe_load"
kind: bug
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m0nxcy12vxk88gp9w80cs400
created_at: 2026-08-22T23:38:13.810Z
updated_at: 2026-08-22T23:52:44.095Z
closed_at: 2026-08-22T23:52:44.095Z
close_reason: arch §14.6 Pass 1 step 2 now names _ruamel_safe_load and says why the pre-check uses the validator's parser. 4bf776b.
---
docs/arch/arch-metaproc-core.md:2019. Code uses _ruamel_safe_load (yaml_repair.py:163,193) and test_self_check_uses_ruamel_yaml_strictness asserts yaml.safe_load must not appear.
