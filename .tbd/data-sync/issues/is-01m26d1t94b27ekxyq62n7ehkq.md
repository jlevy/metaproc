---
type: is
id: is-01m26d1t94b27ekxyq62n7ehkq
title: v0.4.1 release
kind: epic
status: open
priority: 1
version: 1
labels:
  - release
dependencies: []
created_at: 2026-09-10T19:34:56.548Z
updated_at: 2026-09-10T19:34:56.548Z
---
Patch release readying the four commits merged after v0.4.0: the model-catalog work (#69), the RunPool diagnostics and cleanup-ownership fixes (#75), and the UTC shipped-doc date gate (#76), plus the doc/bead reconciliation in #74.

Patch rather than minor: no public CLI flag, process-file field, runtime artifact, plugin entry point, or adapter default was removed or renamed, and no previously accepted model identifier was dropped. The one behavior change is that an unrecognized explicit model name now fails the step instead of silently running the adapter default -- the fix itself, and recorded as a compatibility note in both the CHANGELOG and the release notes.

Release records readied on this branch:
- CHANGELOG [0.4.1] section written from the v0.4.0..HEAD diff, not from commit messages. That was the recorded failure mode of the v0.4.0 notes (mp-lto0), which a post-publication review found missing roughly 23 user-visible changes.
- docs/project/releases/v0.4.1.md written per release-notes-guidelines.
- docs/publishing.md step 5 repointed to the new notes.
- TODO.md Release Follow-Ups reconciled: mp-lto0 is closed and its stale bullet removed; mp-x1qh reworded to the open question that remains.

Remaining scope is the tag itself and the publish it triggers, per docs/publishing.md. TODO.md section Current Release moves to v0.4.1 once that tag exists. The tag was deliberately not cut on this branch.
