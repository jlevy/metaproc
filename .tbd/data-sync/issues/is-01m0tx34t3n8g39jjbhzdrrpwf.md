---
type: is
id: is-01m0tx34t3n8g39jjbhzdrrpwf
title: Cut the 0.3.0 release from main
kind: epic
status: closed
priority: 1
version: 10
labels:
  - release
dependencies: []
child_order_hints:
  - is-01m0tx4fmqn5vnwnap35z6wt9s
  - is-01m0tx4wy1bc33ssm10n3a8ap7
  - is-01m0tx6r53cen6nyye2ap03yme
  - is-01m0tx8jrdfkjcr0n1v2b1qs9q
  - is-01m0txe7ccc7y4yhk2wt77d0dj
  - is-01m0txfcvht87nbn777cm2bstv
  - is-01m0txm9k40pwdbx279nqezdy5
  - is-01m0txmwd5ndrr55r28vcnka4w
created_at: 2026-08-24T22:08:42.306Z
updated_at: 2026-08-25T02:55:26.180Z
closed_at: 2026-08-25T02:55:26.180Z
close_reason: |-
  Released. metaproc 0.3.0 is on PyPI.

  Sequence: PR #39 merged (4cda506) to take the wall-clock flake out of the release path; release prep merged as PR #40 (4d518d8) carrying the completed CHANGELOG, docs/releases/v0.3.0.md, and the cryptography 50.0.0 relock that retired the GHSA-g6cj-pr64-35w5 waiver; CI green on all five jobs at exactly 4d518d8; tagged v0.3.0 there; Publish to PyPI succeeded (run 32803000723).

  Verified after publish: PyPI reports 0.3.0 with AGPL-3.0-or-later, requires-python >=3.12,<4.0, 3.12/3.13/3.14 classifiers, project URLs, and both wheel and sdist. Isolated uvx smoke tests pass for --version (0.3.0), --help, skill metaproc, env --template, and help operator.

  PR #38 and the GTIA v3 stack (#32-#37) were deliberately excluded and ship in the next release.
resolution: null
duplicate_of: null
---
Ship a minor release covering the 103 commits merged since v0.2.1 (2026-08-09), before the larger GTIA v3 stack (#32-#37) lands as a separate follow-up release.

## Goal

A stable, verified 0.3.0 that captures the durable-task-history, retry-feedback, resume-correctness, and GCP dispatch work already on main. The GTIA v3 mapped-composite-scope stack is explicitly OUT of scope and ships later.

## Release gate (docs/publishing.md)

1. Update local main, clean worktree.
2. `make verify` passes.
3. CI green for the exact commit to be tagged.
4. Choose the semantic version.
5. Write release notes per `tbd guidelines release-notes-guidelines` into docs/releases/vX.Y.Z.md.
6. `gh release create vX.Y.Z --target main` (tag drives uv-dynamic-versioning).
7. Watch Publish to PyPI through completion.
8. Verify published PyPI metadata.
9. Isolated `uvx metaproc@X.Y.Z` smoke tests.

## Version choice

0.3.0, not 0.2.2: the release carries a behavior change (code-step outputs are no longer YAML-repaired) plus substantial new capability, which is a minor bump under the 0.x policy already used for 0.2.0 and 0.2.1.

## Blockers

Tracked as children of this epic.
