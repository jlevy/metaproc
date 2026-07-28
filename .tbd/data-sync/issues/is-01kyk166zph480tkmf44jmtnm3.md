---
type: is
id: is-01kyk166zph480tkmf44jmtnm3
title: Package Metaproc as a self-installing Agent Skill
kind: feature
status: closed
priority: 2
version: 4
labels: []
dependencies: []
parent_id: is-01kyk15xd6m1m2vyzexds7xswy
created_at: 2026-07-28T00:14:58.038Z
updated_at: 2026-07-28T00:32:26.206Z
closed_at: 2026-07-28T00:32:26.206Z
close_reason: "Fixed and verified on claude/docs-review-ci-fixes-7re7um: hygiene allowlist for agent no-reply emails (CI lint unblocked); README/docs reorganized into an audience-based map with extraction-residue repairs across 25 files; skill allowed-tools conformance + dogfooded install with drift test. Full make verify green: 3786 passed, 8 skipped."
---
Per tbd cli-agent-skill-patterns guidelines: a simple skill (SKILL.md) that Metaproc can install into consumer repos, delegating to Metaproc's own documentation for details. Evaluate layout requirements, then implement a minimal skill + install path.

## Notes

Reviewed against tbd cli-agent-skill-patterns (L2 self-installer). Machinery already existed (skill/ package, entry-point registry, deterministic compose, 'metaproc skill --install' writing .agents/ + .claude/ copies, 15 tests). Gaps found and fixed: (1) allowed-tools was comma-separated with an inexpressible embedded-space entry; now spec-conformant space-separated 'Bash(metaproc:*) Read'. (2) Repo did not dogfood its own skill and had no drift test; installed committed copies and added test_committed_skill_copies_match_composed_output (skips outside source checkout). Checklist otherwise satisfied: deterministic bundle, idempotent, copy-not-symlink, DO-NOT-EDIT ownership marker, project scope documented in docs/installation.md.
