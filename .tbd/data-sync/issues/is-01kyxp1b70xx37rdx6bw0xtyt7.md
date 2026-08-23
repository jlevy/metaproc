---
type: is
id: is-01kyxp1b70xx37rdx6bw0xtyt7
title: Add checked-JavaScript promise-safety lint overlay
kind: task
status: open
priority: 2
version: 5
spec_path: TODO.md
labels:
  - linting
dependencies: []
parent_id: is-01kzky2kj5g9f2rxfq0wp15q5j
deferred_until: 2026-08-07T00:00:00Z
created_at: 2026-08-01T03:31:42.944Z
updated_at: 2026-08-16T08:00:39.361Z
extensions:
  linear:
    id: d473ac32-9496-4221-adf3-50fafd06f528
    linked_at: 2026-08-16T08:00:39.361Z
---
Adopt the typescript-eslint promise-safety overlay required by the current lint floor once a patched brace-expansion release clears the 14-day third-party cool-off. Attempts with eligible ESLint 10 and ESLint 9 graphs both resolve a high-severity GHSA-mh99-v99m-4gvg-affected brace-expansion; do not weaken the global gate or carry that vulnerable graph.
