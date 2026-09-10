---
type: is
id: is-01m260z1jxnav5y684hgm6xsz4
title: "PR 75 F9: define completion checks for empty or unmaterialized runs"
kind: task
status: open
priority: 2
version: 4
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
labels: []
dependencies:
  - type: blocks
    target: is-01m269w80vt7jb8gjanfp3nfj5
parent_id: is-01m260cd2wnmjm68zd3yqaw799
created_at: 2026-09-10T16:03:42.813Z
updated_at: 2026-09-10T18:48:32.769Z
---
run_status.check_completion accepts zero totals with inactive/absent process state. Decide compatibility-preserving coverage semantics: distinguish an explicitly closed empty expansion from an unlaunched/missing roster, and optionally require expected output/coverage for automation. Keep existing CLI meaning documented in this PR; test both valid no-work runs and missing execution facts before changing exit behavior.
