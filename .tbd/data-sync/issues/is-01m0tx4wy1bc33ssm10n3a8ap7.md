---
type: is
id: is-01m0tx4wy1bc33ssm10n3a8ap7
title: "Land PR #39 to get main green before tagging 0.3.0"
kind: task
status: open
priority: 0
version: 1
labels:
  - release,ci
dependencies: []
parent_id: is-01m0tx34t3n8g39jjbhzdrrpwf
created_at: 2026-08-24T22:09:39.776Z
updated_at: 2026-08-24T22:09:39.776Z
---
## Blocker

CI is currently RED on main. The latest run (32743043484, merge of PR #30, 2026-08-24T15:08) failed on `test (3.14)`:

```
FAILED tests/execution_model/test_scale.py::TestEnvelope::test_readiness_does_not_degrade_quadratically_with_width
AssertionError: one scheduling pass: 0.0355s at width 800, 0.2314s at width 3200
(6.5x for 4x the work; linear is 4x, a scan measures about 8x)
assert 6.525754197734022 < 6.0
```

lint, distribution, and test (3.12/3.13) all passed. This is the known wall-clock flake, not a functional regression.

## Fix already exists

PR #39 (`test(execution): make scale guard deterministic`, head 8d37591) replaces the noisy cross-width timing ratio with a deterministic equality-work complexity oracle. It is small (2 files, +63/-38, 1 commit), targets main, and reports `mergeable_state: clean`. Its own CI run 32763381055 passed on 3.12/3.13/3.14, and a forced tuple-scan mutation is still rejected (5,121,600 comparisons vs the 12,800 ceiling), so the guard keeps its discriminating power.

## Action

Merge PR #39, confirm the resulting main CI run is green on all three Python versions, and tag 0.3.0 from that commit. Step 3 of the release checklist requires CI green for the exact commit being tagged.
