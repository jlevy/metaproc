---
type: is
id: is-01m0vhr5rv34k6cbvr6wqx24sw
title: Consolidate mapped-scope runtime fixes on released main
kind: task
status: in_progress
priority: 0
version: 5
spec_path: null
labels:
  - execution-model
dependencies:
  - type: blocks
    target: is-01m0vhs620ptcvxv074ccx88z4
parent_id: is-01m0r92q2y1pe7dmhrcj6nst7q
child_order_hints:
  - is-01m0vqngx1ergbsmwwcn9mtz8x
created_at: 2026-08-25T04:09:42.938Z
updated_at: 2026-08-25T16:59:22.188Z
---
Create one clean replacement branch from released main. Carry forward only the still-needed generic behavior from pull requests 32, 33, 34, 35, 43, 37, and 47; exclude the superseded retry-later proposal and unrelated work. Preserve review domains in tests and documentation without importing consumer-specific plans, evidence, or commit history. Do not merge.
