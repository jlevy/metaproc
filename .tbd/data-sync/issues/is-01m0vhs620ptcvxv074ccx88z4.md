---
type: is
id: is-01m0vhs620ptcvxv074ccx88z4
title: Verify the consolidated mapped-scope runtime head
kind: task
status: open
priority: 0
version: 10
spec_path: null
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
updated_at: 2026-08-25T16:59:21.926Z
---
Single pre-smoke gate for the consolidated Metaproc runtime changes. After every dependency has a fixed, rebutted, or explicitly deferred disposition, run focused failure tests for each review domain, full make verify on the clean replacement head, exact-head GitHub CI, and a diff audit against released main. Closing this gate permits a private downstream network-free smoke test; it does not authorize provider concurrency or merge.
