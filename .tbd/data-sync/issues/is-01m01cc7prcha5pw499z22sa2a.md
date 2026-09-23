---
type: is
id: is-01m01cc7prcha5pw499z22sa2a
title: Bootstrap the pinned Node and uv toolchain in agent session hooks
kind: task
status: closed
priority: 2
version: 4
labels: []
dependencies: []
created_at: 2026-08-15T00:15:33.581Z
updated_at: 2026-08-23T05:42:57.400Z
closed_at: 2026-08-15T01:13:42.201Z
close_reason: "Shipped in PR #18 (commit 5895b5f): devtools/ensure-toolchain.sh installs the repo's pinned Node and uv with per-platform checksum verification; one shared copy wired into both .claude/settings.json and .codex/hooks.json ahead of the tbd hook; check_supply_chain.py fails on pin drift or a missing agent wiring. make verify green (3946 passed, 8 skipped); hook verified live on a session resume."
---
Agent sessions (Claude Code and Codex) start without Node or uv present, so 'make verify' and every uv/npm command fail until a human-or-agent installs them by hand. Add a shared, agent-neutral session-bootstrap script that installs the repo's PINNED Node and uv (never 'latest', per SUPPLY-CHAIN-SECURITY.md and package.json engine-strict), wire it into both .claude/settings.json and .codex/hooks.json, and guard the pins against drift in check_supply_chain.py.

## Notes

Shipped via PR #19 on branch `claude/docs-review-ci-fixes-7re7um`, branched fresh from
`main` at v0.2.1. (The `close_reason` above names PR #18; that PR was closed in favor of
#19, which carries the same work plus the tbd upgrade. #19 is the one to look at.)

Four commits:

- `5895b5f` — `devtools/ensure-toolchain.sh`, both agent wirings, and the
  `check_supply_chain.py` drift guard.
- `55f5cc9` — npm global-prefix fix, so `npm install -g` lands on PATH after the script
  installs its own Node. Reproduced live: without it, `npm install -g get-tbd` exits 0
  and `tbd` still does not resolve.
- `8507211` — `docs/agent-toolchain-bootstrap.md`, the reusable pattern and when a
  provisioned image or an existing version manager is the better answer.
- `b529452` — adopt `get-tbd` 0.6.5 under the first-party cool-off exemption; migrates
  this repo's tbd format f06 to f07 and regenerates the managed surfaces.

Verification: `make verify` green (3,946 passed / 8 skipped); CI green on the head
commit (lint, test 3.12/3.13/3.14, distribution); the hook verified live across session
resumes; the drift guard negative-tested in both directions.

Upstream outcome: the diagnostic half of this work was contributed to `jlevy/tbd` and
merged as PR #248 (`c4dd9f38`) — a `tbd doctor` check that names an unreachable npm
global bin directory, plus an `agent-session-bootstrap` guideline generalizing the
pattern. That is complementary, not a replacement: tbd's check is diagnostic and
repo-agnostic, while this script is preventive and installs *this* repo's pins, which
tbd has no way to know.
