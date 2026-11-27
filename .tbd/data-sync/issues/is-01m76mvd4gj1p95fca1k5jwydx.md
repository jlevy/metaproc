---
type: is
id: is-01m76mvd4gj1p95fca1k5jwydx
title: "PR #121 A14: exact_inputs consumer of a never step's output is reused over new bytes"
kind: bug
status: closed
priority: 3
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76msm7xe6a3n7dcegpafnrx
hold: null
hold_until: null
created_at: 2026-11-12T01:09:44.207Z
updated_at: 2026-11-12T01:09:45.212Z
started_at: 2026-11-12T01:09:44.794Z
closed_at: 2026-11-12T01:09:45.210Z
close_reason: "Fixed by removal in 59397b6: no exact_inputs consumer exists, and planning refuses exact_inputs. Design 10.2 keeps the documented rule that a downstream step follows its own policy. The idea of cascading from a never step whose outputs changed is recorded for the design in mp-gg7r."
resolution: null
duplicate_of: null
---
Low (suggestion). Design 10.2/10.3.

Review: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
PR: #121, reviewed head dee3914e5e001aace3abc728d81b0c6f859845e1
