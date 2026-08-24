---
type: is
id: is-01m0txe7ccc7y4yhk2wt77d0dj
title: "make verify cannot run in a fresh agent container: uv and Node floors both unmet"
kind: bug
status: open
priority: 1
version: 1
labels:
  - tooling,dx
dependencies: []
parent_id: is-01m0tx34t3n8g39jjbhzdrrpwf
created_at: 2026-08-24T22:14:45.388Z
updated_at: 2026-08-24T22:14:45.388Z
---
## Symptom

A fresh Claude Code remote container cannot run the required handoff gate. Two independent toolchain floors are unmet, and each only surfaces after the previous one is fixed by hand:

1. `uv sync --locked` fails under the container's uv 0.8.17 (see mp-flfr for the misleading error).
2. After installing uv 0.12.4 by hand, `npm ci` fails:

```
npm error notsup Required: {"node":">=24.18.0 <25","npm":">=11.10.0 <12"}
npm error Actual:   {"npm":"10.9.7","node":"v22.22.2"}
```

The container images ship node20/21/22 only; nothing satisfies `>=24.18.0 <25` out of the box. Getting to a runnable gate took a manual `uv tool install uv==0.12.4` plus `nvm install 24`.

## Why this matters for the release

`make verify` is the required handoff gate and step 2 of the release checklist. Any agent or contributor working from a standard container currently cannot execute it without undocumented manual setup, so "verify passed" is not reproducible on demand. That is a poor position from which to certify a release.

## Existing work

PR #19 (`feat(devtools): self-installing pinned toolchain for agent sessions, guarded against drift`) targets exactly this and has been open since 2026-08-15. It is the structural fix for both floors.

Note that PR #38's own validation notes report the same class of problem from the other direction (uv 0.12.4 reading the relative 14-day window as lock drift), so this is costing time across multiple branches.

## Findings that may retire adjacent beads

`uv sync --all-extras --all-groups --locked` completed cleanly under uv 0.12.4 in this container against main at 6819ddd, with no lock mutation and no drift error. mp-usjd was filed on the opposite observation. Recheck whether it still reproduces before spending time on it; the difference may be the moving 14-day window rather than the uv version.

## Action

Either land PR #19, or document the exact required toolchain versions and bootstrap steps in docs/development.md so the gate is runnable from a clean container.
