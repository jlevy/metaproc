---
type: is
id: is-01m0wfhf7yd1mpqx1pdh0rbepj
title: Treat completed pressure checks as healthy trace events
kind: bug
status: closed
priority: 2
version: 3
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - operator-smoke
dependencies: []
parent_id: is-01m0vhs620ptcvxv074ccx88z4
created_at: 2026-08-25T12:50:20.541Z
updated_at: 2026-08-25T13:19:02.518Z
closed_at: 2026-08-25T13:19:02.518Z
close_reason: "Fixed at b5c4721; regressions, full 4385-test gate, pre-push gate, and successful-run operator replay passed. PR #37 has no checks because its stacked base is not main; exact-pin consumer CI remains the independent gate."
resolution: null
duplicate_of: null
---
Trace health on a successful GTIA parent run reported 25 partial metaproc-engine spans solely because instantaneous pressure_check observations are hardcoded partial. These are completed scheduler observations, not incomplete spans; emit status ok and preserve pressure level as attributes.
