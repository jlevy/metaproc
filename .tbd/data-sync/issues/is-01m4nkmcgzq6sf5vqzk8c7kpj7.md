---
type: is
id: is-01m4nkmcgzq6sf5vqzk8c7kpj7
title: Count quota_exhausted in pool status FailureCounts and surface the quota hold in metaproc stats
kind: task
status: open
priority: 3
version: 3
labels: []
dependencies: []
parent_id: is-01m4my9r60j9s9nkm8t1rnam6f
created_at: 2026-10-11T13:49:56.637Z
updated_at: 2026-10-11T14:27:49.061Z
---
Deferred from PR #106 review A7 part (b) (https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5482282352). RunPool counts quota_exhausted in _failure_class_counts, but FailureCounts (src/metaproc/runpool/status.py) has no quota_exhausted field and _build_status drops the key, so pool status shows a cut provider ceiling and DSQ-bound without the count that caused it. Adding the field touches every reader of FailureCounts: pool._build_status, stats/engine.py (per-field worker aggregation), stats/analysis.py, stats/formatters.py (two blocks), the Metabrowser plugin domain_views.js, the GCP rollups, and the runpool golden snapshots. Related: mp-m4vi (subdivide invalid_output). Waits on nothing; it is separate from PR #106 because of that reader surface.

## Notes

Also covers review C5 on PR #106 (https://github.com/jlevy/metaproc/pull/106#pullrequestreview-5483637641), same reader surface: stats/engine.py aggregates ControllerStatus field by field into stats/models.py and drops quota_hold_remaining_s, which the GCP rollup carries. Add it to the stats model as the longest hold across workers and print it next to Recent 429s in stats/formatters.py.
