---
type: is
id: is-01m0vhs620ptcvxv074ccx88z4
title: Verify the consolidated mapped-scope runtime head
kind: task
status: in_progress
priority: 0
version: 14
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
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
updated_at: 2026-08-25T19:31:34.109Z
---
Single pre-smoke gate for the clean consolidated runtime. After each known finding has a fixed, rebutted, or explicitly deferred disposition, run focused failure tests, complete make verify, audit the diff and public boundary, and wait for exact-head GitHub CI. Closing this gate permits an immutable downstream M0 pin; it does not authorize provider concurrency or merge.

## Notes

Local exact-head gate passed on the clean working tree: make verify completed with 4,408 passed, 8 skipped; lint, docs, public hygiene, supply-chain checks, browser checks, audits, build, distribution inspection, and installed-wheel smoke all passed. Remaining: commit, draft PR, exact-head GitHub CI, then close this gate before downstream M0.
