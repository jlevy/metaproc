---
type: is
id: is-01m0zfmahvt4gkgb6458mbwctd
title: Move the core documentation set into src/metaproc/docs
kind: task
status: open
priority: 1
version: 15
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies:
  - type: blocks
    target: is-01m0zfmnmjz12f0evrmddyh8az
  - type: blocks
    target: is-01m0zfmp3h1fefdr5bc88zp9c8
  - type: blocks
    target: is-01m0zfmpjey3fp75kgm27agrbz
  - type: blocks
    target: is-01m0zg02jxkanjfngqpypfb6jn
  - type: blocks
    target: is-01m10z8xv335zd4zcknjmnbct8
  - type: blocks
    target: is-01m10z8z9aynagg69rfanemcnf
  - type: blocks
    target: is-01m10z8ypvc86kvrgc42sd8xpy
  - type: blocks
    target: is-01m0zfmbsnfzgsk3esdrrqgrf7
  - type: blocks
    target: is-01m10za7jwtfxc4kan59ca5q1a
  - type: blocks
    target: is-01m10za7vryv4mrqvcq98jqr5c
  - type: blocks
    target: is-01m10za84cxdzqqen0qa9w4x6d
  - type: blocks
    target: is-01m10za6g75kbhq91bb8pmfk22
  - type: blocks
    target: is-01m0zfmbchshq1m9evnysb8ctx
parent_id: is-01m0zfkvtf12ag7e6y0rbdg7mw
created_at: 2026-08-26T16:49:37.339Z
updated_at: 2026-08-27T06:43:40.106Z
---
Phase 1, mechanical. No prose edits beyond what a link rewrite requires.

git mv into src/metaproc/docs/:
- docs/arch/arch-metaproc-core.md -> metaproc-design.md (105 refs across 52 tracked files, excluding .tbd/; the largest sweep in this plan)
- docs/arch/arch-authentication.md, arch-cloud-execution.md, arch-runpool.md, arch-claude-code-harness.md, arch-execution-model.md, arch-testing.md, arch-file-io-utilities.md (keep filenames)
- docs/conventions.md, docs/artifact-catalog.md, docs/process-framework-concepts.md, docs/execution-model-design.md

Then remove the now-empty docs/arch/.

Inbound link sweep covers: README.md, AGENTS.md, docs/development.md, docs/installation.md, all docs/runbooks/*.md, docs/project/**, the three existing manuals in src/metaproc/docs/, Python docstrings in src/metaproc/execution_model/, and the path constant in tests/test_locking_policy.py.

Verify with 'uv run python -m devtools.check_links' after each mv, not once at the end - it is the only thing standing between this and 105 broken references.
