---
type: is
id: is-01kyk15xd6m1m2vyzexds7xswy
title: "Alpha stabilization: green CI, organized docs, agent-skill packaging"
kind: epic
status: closed
priority: 1
version: 7
labels: []
dependencies: []
child_order_hints:
  - is-01kyk166a6dv2nhfepdnjjb57m
  - is-01kyk166pgxn3tykf54yqf5e0d
  - is-01kyk166zph480tkmf44jmtnm3
created_at: 2026-07-28T00:14:48.230Z
updated_at: 2026-08-14T02:40:38.202Z
closed_at: 2026-08-14T02:40:37.931Z
close_reason: "PR #2 merged into main (92c0651) after an owner-side review cycle: branch was rebuilt as one clean commit (83b894d) on then-current main, keeping the docs reorganization, skill conformance + dogfooded drift test, and extraction-residue repairs; the interim email allowlist was superseded by main's narrower Git-metadata trailer normalization. CI green; v0.2.1 has since been released on top."
---
Make the standalone repo alpha-level stable and usable by multiple downstream repos: fix the CI lint failure, systematically reorganize documentation entry points (README + contextual docs), and package Metaproc so it can install itself as an Agent Skill that delegates to its own docs.

## Notes

PR #2 (https://github.com/jlevy/metaproc/pull/2) open from claude/docs-review-ci-fixes-7re7um to main: CI green on merged tip ef53cae (lint, 3.12/3.13/3.14 tests, distribution), mergeable_state clean, Cursor Bugbot review posted Low Risk with no findings. Branch includes origin/main through 9dfc34d. Children mp-2lzk/mp-mc9u/mp-h88t closed. Epic closes when PR merges.
