---
type: is
id: is-01m01cc7prcha5pw499z22sa2a
title: Bootstrap the pinned Node and uv toolchain in agent session hooks
kind: task
status: closed
priority: 2
version: 3
labels: []
dependencies: []
created_at: 2026-08-15T00:15:33.581Z
updated_at: 2026-08-15T01:34:33.625Z
closed_at: 2026-08-15T01:13:42.201Z
close_reason: "Shipped in PR #18 (commit 5895b5f): devtools/ensure-toolchain.sh installs the repo's pinned Node and uv with per-platform checksum verification; one shared copy wired into both .claude/settings.json and .codex/hooks.json ahead of the tbd hook; check_supply_chain.py fails on pin drift or a missing agent wiring. make verify green (3946 passed, 8 skipped); hook verified live on a session resume."
---
Agent sessions (Claude Code and Codex) start without Node or uv present, so 'make verify' and every uv/npm command fail until a human-or-agent installs them by hand. Add a shared, agent-neutral session-bootstrap script that installs the repo's PINNED Node and uv (never 'latest', per SUPPLY-CHAIN-SECURITY.md and package.json engine-strict), wire it into both .claude/settings.json and .codex/hooks.json, and guard the pins against drift in check_supply_chain.py.

## Notes

Shipped via PR #19 (supersedes the closed #18, same head). Commits: 5895b5f bootstrap script + both agent wirings + check_supply_chain drift guard; 55f5cc9 npm global-prefix fix so globally installed tools stay on PATH; 8507211 docs/agent-toolchain-bootstrap.md recording the reusable pattern and when a provisioned image or version manager is the better answer. make verify green (3946 passed, 8 skipped); CI green on the first two commits; hook verified live across two session resumes.
