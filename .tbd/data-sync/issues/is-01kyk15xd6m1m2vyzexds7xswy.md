---
type: is
id: is-01kyk15xd6m1m2vyzexds7xswy
title: "Alpha stabilization: green CI, organized docs, agent-skill packaging"
kind: epic
status: open
priority: 1
version: 5
labels: []
dependencies: []
child_order_hints:
  - is-01kyk166a6dv2nhfepdnjjb57m
  - is-01kyk166pgxn3tykf54yqf5e0d
  - is-01kyk166zph480tkmf44jmtnm3
created_at: 2026-07-28T00:14:48.230Z
updated_at: 2026-07-28T00:32:26.451Z
---
Make the standalone repo alpha-level stable and usable by multiple downstream repos: fix the CI lint failure, systematically reorganize documentation entry points (README + contextual docs), and package Metaproc so it can install itself as an Agent Skill that delegates to its own docs.

## Notes

All three children closed on branch claude/docs-review-ci-fixes-7re7um (commits a255dce, 40ec99c, 99994fe). make verify green locally. Epic stays open until CI on the branch confirms green and the work merges to main.
