---
type: is
id: is-01m0r7j309xchx852txntmzpx4
title: "PR #29 review S2: cover content then transport retry sequence"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0r7hpfe75sqfg3vecc7j8fr
created_at: 2026-08-23T21:13:54.440Z
updated_at: 2026-08-23T21:26:27.920Z
closed_at: 2026-08-23T21:26:27.920Z
close_reason: null
---
Non-blocking suggestion: add a mixed-sequence regression showing content failure feedback survives a later transport failure and is still supplied to the following retry.
