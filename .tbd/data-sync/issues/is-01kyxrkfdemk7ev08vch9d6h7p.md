---
type: is
id: is-01kyxrkfdemk7ev08vch9d6h7p
title: Prepare the Metaproc 0.2.0 release pull request
kind: task
status: closed
priority: 1
version: 5
labels:
  - release
dependencies:
  - type: blocks
    target: is-01kyx38gn4gwmp93rst4psbm0x
parent_id: is-01kyx37mj1agq5zha1x5gn574f
created_at: 2026-08-01T04:16:34.222Z
updated_at: 2026-08-01T04:37:24.475Z
closed_at: 2026-08-01T04:36:09.400Z
close_reason: "Prepared release-only PR #4 at f72d414; local make verify passed 3,793 tests with 8 expected skips, exact v0.2.0 tag simulation built and validated both distributions, and hosted CI run 30684107078 passed all checks."
---
Prepare a release-only pull request stacked on PR #3. Reconcile the unreleased 0.1.0 documentation with the actual registry and tag state; finalize the 0.2.0 changelog, release notes, installation examples, publishing instructions, and standalone release validation. Commit, push, open a ready PR, and wait for CI. Do not create the irreversible tag or publish to PyPI in this bead.

## Notes

Release PR: https://github.com/jlevy/metaproc/pull/4 at f72d414. Senior release-readiness review: https://github.com/jlevy/metaproc/pull/4#issuecomment-5149822436. Local make verify passed 3,793 tests with 8 expected skips; exact v0.2.0 tag simulation built and validated both distributions; hosted CI run 30684107078 passed all checks. PR is restored to base feat/softschema-0.3 with merge state CLEAN.
