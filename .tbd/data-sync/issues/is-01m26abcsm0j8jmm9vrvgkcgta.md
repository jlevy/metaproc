---
type: is
id: is-01m26abcsm0j8jmm9vrvgkcgta
title: Permit and encourage direct raw-log debugging in operator guidance
kind: task
status: closed
priority: 1
version: 4
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies: []
parent_id: is-01m26a2gvmth1jjgrkg9fs1vha
created_at: 2026-09-10T18:47:44.691Z
updated_at: 2026-09-10T19:11:03.577Z
closed_at: 2026-09-10T19:11:03.577Z
close_reason: Completed in PR 75 commit 5546a02; consolidated plans and issue graph, corrected direct source-log operator guidance, passed make verify (4684 passed, 8 skipped) and all five CI checks. See notes for evidence; runtime findings and parent features remain open.
resolution: null
duplicate_of: null
---
Correct the existing operator manual and skill blanket prohibition on raw file inspection. Keep CLI-owned orchestration and state mutation, but explicitly encourage read-only inspection of original per-agent stdout/stderr/native transcripts for debugging, with documented paths, gzip access, cause/evidence reporting, and current diagnostic limitations. Regenerate the skill copies and validate shipped help. This documentation correction does not claim the planned uniform per-attempt log-link UI is implemented.

## Notes

Implemented in PR 75 commit 5546a02665bc0d43f4dc809b9b4d87f85e7f13b6. The operator manual and skill baseline now encourage direct read-only inspection of captured per-attempt output and available native agent logs. Guidance includes current task-log paths, gzip and live-tail examples, attempt correlation, observed-cause/evidence reporting, unknown or unavailable evidence, and the difference between original capture and derived trace views. It explicitly discloses combined local stdout/stderr and Pi events filtered before the retained task log. CLI ownership of orchestration and state mutation remains clear.

Both generated skill copies were regenerated. Full make verify and the pre-push gate passed with 4684 tests and 8 skips, including skill drift, shipped help/link/hygiene, audits, distribution, and installed-wheel checks. All five CI checks passed: https://github.com/jlevy/metaproc/actions/runs/34518494536. Parent mp-83g2 remains open for runtime source-log locators and raw pre-filter capture; mp-5les remains open for prominent typed diagnostics.
