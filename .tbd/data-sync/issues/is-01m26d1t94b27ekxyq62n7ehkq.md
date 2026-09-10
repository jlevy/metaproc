---
type: is
id: is-01m26d1t94b27ekxyq62n7ehkq
title: v0.4.1 release
kind: epic
status: closed
priority: 1
version: 3
labels:
  - release
dependencies: []
created_at: 2026-09-10T19:34:56.548Z
updated_at: 2026-09-10T19:56:08.249Z
closed_at: 2026-09-10T19:56:08.245Z
close_reason: |-
  Published. Tag v0.4.1 points at 36ad4fa, the squash merge of pull request 77 and the tip of main, so there is no gap between the published wheel and post-release main.

  Evidence, in publishing.md order:
  - Step 2 release gate: make verify green on 36ad4fa (4,684 passed, 8 skipped; installed-wheel smoke on metaproc 0.4.1.dev5+36ad4fa; distribution checks on both artifacts).
  - Step 3 CI on the exact commit tagged: run 34522585014, green on lint, distribution, and Python 3.12, 3.13 and 3.14.
  - Steps 6-7 publish: run 34522898479 succeeded end to end, including its own release gate and the tag-versus-package-version verification.
  - Step 8 PyPI metadata: version 0.4.1, AGPL-3.0-or-later, requires-python >=3.12,<4.0, 3.12/3.13/3.14 classifiers, homepage/repository/issues/changelog/documentation links, and both metaproc-0.4.1-py3-none-any.whl and metaproc-0.4.1.tar.gz present.
  - Step 9 isolated smoke: uvx metaproc@0.4.1 --version (0.4.1), --help, skill metaproc, and env --template all succeeded from a clean environment outside the checkout.
  - GitHub release body is byte-identical to docs/project/releases/v0.4.1.md apart from the trailing newline GitHub appends; not a draft, not a prerelease.

  TODO.md section Current Release moved to v0.4.1 and the Active Development entry dropped, its remaining scope having been the tag itself. mp-x1qh stays open: the respectGitIgnore default is unsettled and disclosure does not settle it.
resolution: null
duplicate_of: null
---
Patch release readying the four commits merged after v0.4.0: the model-catalog work (#69), the RunPool diagnostics and cleanup-ownership fixes (#75), and the UTC shipped-doc date gate (#76), plus the doc/bead reconciliation in #74.

Patch rather than minor: no public CLI flag, process-file field, runtime artifact, plugin entry point, or adapter default was removed or renamed, and no previously accepted model identifier was dropped. The one behavior change is that an unrecognized explicit model name now fails the step instead of silently running the adapter default -- the fix itself, and recorded as a compatibility note in both the CHANGELOG and the release notes.

Release records readied on this branch:
- CHANGELOG [0.4.1] section written from the v0.4.0..HEAD diff, not from commit messages. That was the recorded failure mode of the v0.4.0 notes (mp-lto0), which a post-publication review found missing roughly 23 user-visible changes.
- docs/project/releases/v0.4.1.md written per release-notes-guidelines.
- docs/publishing.md step 5 repointed to the new notes.
- TODO.md Release Follow-Ups reconciled: mp-lto0 is closed and its stale bullet removed; mp-x1qh reworded to the open question that remains.

Remaining scope is the tag itself and the publish it triggers, per docs/publishing.md. TODO.md section Current Release moves to v0.4.1 once that tag exists. The tag was deliberately not cut on this branch.

## Notes

Release records readied on branch claude/lucid-archimedes-mvu2er and proposed in https://github.com/jlevy/metaproc/pull/77. make verify green (4,684 passed, 8 skipped, installed-wheel smoke on metaproc 0.4.1.dev4+7e49f57); PR CI green on lint, distribution, and Python 3.12, 3.13 and 3.14. Remaining scope is the tag and the publish it triggers.
