---
type: is
id: is-01m0tx4fmqn5vnwnap35z6wt9s
title: make install gives an inscrutable TOML error on uv versions older than required-version
kind: bug
status: in_progress
priority: 1
version: 2
labels:
  - tooling,dx
dependencies: []
parent_id: is-01m0tx34t3n8g39jjbhzdrrpwf
created_at: 2026-08-24T22:09:26.167Z
updated_at: 2026-08-24T23:38:41.006Z
---
## Symptom

On a fresh checkout with uv 0.8.17, `make verify` dies in its `install` prerequisite:

```
error: invalid value '14 days' for '--exclude-newer <EXCLUDE_NEWER>': `14 days` could not be parsed as a valid date: failed to parse year in date "14 days": failed to parse "14 d" as year (a four digit integer): invalid digit, expected 0-9 but got
make: *** [Makefile:25: install] Error 2
```

Nothing in that message says the real problem: the uv binary is too old.

## Root cause

`uv.toml` declares `required-version = ">=0.11.26"`, but that gate is never reached. uv deserializes the config file before evaluating `required-version`, and `exclude-newer = "14 days"` (relative-duration syntax) is not valid in older uv, so TOML parsing fails first.

Reproduced directly with a throwaway project and uv 0.8.17:

- `required-version = ">=99.0.0"` alone -> `error: Required uv version >=99.0.0 does not match the running version 0.8.17. Update uv by running uv self update.` (correct, actionable)
- `required-version = ">=99.0.0"` plus `exclude-newer = "14 days"` -> `error: Failed to parse uv2.toml / TOML parse error at line 2, column 17` (the version gate never fires)

So `required-version` is dead code for precisely the uv versions it exists to reject. Confirmed the same config resolves cleanly under uv 0.12.4.

## Impact

Any contributor or agent arriving with an older uv is sent chasing a date-parsing bug in the cool-off window instead of upgrading uv. This cost real time during the 0.3.0 release review.

## Options

- Move the relative window out of `uv.toml` (the Makefile already exports `UV_EXCLUDE_NEWER ?= 14 days`) so the config file stays parseable by old uv and `required-version` can fire.
- Add an explicit uv-version preflight to the `install` target that checks the binary against the floor and prints the upgrade instruction.
- Adopt the pinned self-installing toolchain from PR #19, which addresses this structurally.

## Related

mp-bnx0 (rolling cool-off vs locked verification) and mp-usjd (reproducibility across uv versions) are adjacent but distinct; this one is specifically about the misleading failure mode.

## Notes

Addressed by the PR #19 refresh on 2026-08-24, from both directions.

The uv floor moved to a bounded range (required-version = ">=0.12.0,<0.13"), and devtools/ensure-toolchain.sh installs a checksum-verified uv 0.12.3 at session start, so an agent arriving with an old uv gets a working toolchain instead of the misleading '14 days could not be parsed as a valid date' TOML error.

Note the underlying uv behaviour is unchanged: required-version is still evaluated after config deserialization, so a sufficiently old uv reading this uv.toml directly still fails on the relative exclude-newer before the version gate fires. The bootstrap is what stops anyone hitting it. Leave this bead open if the repo wants an explicit preflight message as well.
