---
type: is
id: is-01m0tx8jrdfkjcr0n1v2b1qs9q
title: "Hold PR #38 out of 0.3.0: it removes public CLI surfaces"
kind: task
status: closed
priority: 1
version: 2
labels:
  - release,scope
dependencies: []
parent_id: is-01m0tx34t3n8g39jjbhzdrrpwf
created_at: 2026-08-24T22:11:40.429Z
updated_at: 2026-08-25T02:37:49.826Z
closed_at: 2026-08-25T02:37:49.826Z
close_reason: "Held as planned. PR #38 remains open and out of 0.3.0; it ships with the GTIA v3 stack in the following release, where its CLI removals get a migration note."
resolution: null
duplicate_of: null
---
## Decision

Keep PR #38 (`refactor(gcp): retire gateway and hybrid compatibility paths`) out of the 0.3.0 release. Ship it with the next release alongside the GTIA v3 stack.

## Why

PR #38 is open against main and reports `mergeable_state: clean`, so it *could* be merged before tagging. It should not be. It deletes public CLI surface:

- `gcp archive`, `gcp remote`, `gcp remote-run`, `gcp self-install`
- remote/run-ID routing from filesystem `status`
- `status --cloud-runs-dir`, `validate --cloud-runs-dir`, `pool retry-missing`
- workstation Filestore identity and cloud-credential heuristics

It also requires the internal `gcp-worker` backend to run under `--cloud` or inside GCP Batch, and changes Filestore to live/restart scratch with terminal durable publication moved to downstream consumers.

AGENTS.md: "Preserve public CLI flags, process-file fields, runtime artifact shapes, plugin entry points, and Agent Skill behavior unless the change includes a migration plan."

The stated intent for 0.3.0 is a stable set of improvements over v0.2.1, with the larger work landing as a separate release. A 40-file, -2,806-line refactor that removes seven documented CLI surfaces is that larger work. Mixing it in turns a clean minor release into one that needs a migration story, and it muddies the bisect boundary if anything in the release regresses.

## Consequence for 0.3.0 scope

0.3.0 = current main HEAD (6819ddd) plus PR #39 (the deterministic scale guard) and the CHANGELOG completion from mp-bn76. Nothing else.

## Follow-up for the next release

When #38 does land, it needs a documented migration plan and a Breaking section in the release notes naming each removed surface and its replacement. PR #38's body already asserts these were "historical runtime paths, not compatibility needed by ordinary local runs" - that claim should be the basis of the note, and should be confirmed against any known consumer before it ships.
