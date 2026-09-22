---
type: is
id: is-01m355rwfy7z9ag5v8kr7f1zfm
title: "PR 92 R5: preserve credential owner access under restrictive umask"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m355h6et8q600r0pv9c30k49
created_at: 2026-09-22T18:24:11.254Z
updated_at: 2026-09-22T18:24:11.254Z
---
PR 92 review R5: io/secret_io.py:62 publishes mode 0000 under umask 0777, whereas old chmod restored 0600. Apply fchmod before secret bytes and test restrictive umask.
