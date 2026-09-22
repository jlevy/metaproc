---
type: is
id: is-01m355rycracg9qppw8h3qnfgk
title: "PR 92 R6: distinguish exclusive creation from atomic publication"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m355h6et8q600r0pv9c30k49
created_at: 2026-09-22T18:24:13.204Z
updated_at: 2026-09-22T18:24:13.204Z
---
PR 92 review R6: arch-file-io-utilities.md:115 wrongly promises complete-content atomic visibility for open x and mkdir claims. Correct documentation without changing runtime claims or gate scope.
