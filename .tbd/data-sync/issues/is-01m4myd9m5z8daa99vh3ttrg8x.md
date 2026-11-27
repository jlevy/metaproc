---
type: is
id: is-01m4myd9m5z8daa99vh3ttrg8x
title: "PR #105 B2: cwd .gemini/ and .env fully trusted under --skip-trust (pre-existing)"
kind: bug
status: open
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m4mybyqptgk7zm6660wmwtzb
created_at: 2026-10-11T07:39:04.174Z
updated_at: 2026-10-11T13:37:09.809Z
---
Medium, pre-existing, outside the diff. Review B https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482362930. A cwd .gemini/settings.json can add hooks, mcpServers, tools.discoveryCommand/callCommand, security.auth, telemetry, and expand env vars; findEnvFile loads cwd .gemini/.env or nearest .env. Fix (pick one): document the boundary; extend the B1 guard to those keys; or evaluate launching untrusted (GEMINI_CLI_TRUST_WORKSPACE=false, no --skip-trust) for non-operator cwds. Reviewer: track as a bead, not a merge condition.

## Notes

Deferred from PR #105 (review B, B2; pre-existing, outside the diff). Option (a), stating the boundary, landed with B1 in commit 535fff9: the credentials runbook says a Gemini step's working directory must be operator-authored, never writable by agent steps and never a third-party checkout. Remaining work waits on a design decision between (b) extending the working-directory guard to hooks, mcpServers, tools.discoveryCommand/callCommand, security.auth and telemetry, and (c) launching untrusted (GEMINI_CLI_TRUST_WORKSPACE=false, no --skip-trust) for working directories that are not operator-authored, which needs the approval-mode behaviour of untrusted folders and the 0.40 floor checked first.
