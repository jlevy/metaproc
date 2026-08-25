---
type: is
id: is-01m0vhs620ptcvxv074ccx88z4
title: "Prepare consolidated #32-#37 head for GTIA L0"
kind: task
status: open
priority: 0
version: 9
spec_path: docs/project/specs/active/plan-2026-08-23-native-mapped-composite-scopes.md
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0t4v9e0tas8gh2t745exy3z
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
child_order_hints:
  - is-01m0vykwfjbp8gqe73nt1bh7kp
  - is-01m0vz82a3231p6v4ra7ecsf6w
  - is-01m0wfher3044jkd78877hrf0j
  - is-01m0wfhf03q07b1m3hd8cxw1f1
  - is-01m0wfhf7yd1mpqx1pdh0rbepj
  - is-01m0wfhffpxa70k40qbyxkdcyk
  - is-01m0wg34819k1mk34rfh9pm007
created_at: 2026-08-25T04:10:15.999Z
updated_at: 2026-08-25T12:59:59.104Z
---
Single pre-smoke gate for the retained Metaproc stack. After every dependency has a fixed or explicitly reviewed disposition, run focused failure tests at each repaired layer, full make verify on the consolidated pull request 37 head, exact-head GitHub CI on every stack level, and verify the PR diff/bases match the tested commit map. Publish the per-finding disposition maps. Closing this gate authorizes a clean pinned GTIA network-free L0; it does not authorize live provider concurrency or merge by itself.
