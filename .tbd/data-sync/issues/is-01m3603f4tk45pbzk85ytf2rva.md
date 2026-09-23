---
type: is
id: is-01m3603f4tk45pbzk85ytf2rva
title: "PR 96 R2: persist logical identity bindings across spec and alias changes"
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m35zgrcms4fcyvf39xd6wr4j
created_at: 2026-09-23T02:04:21.016Z
updated_at: 2026-09-23T02:04:21.016Z
---
Design review of https://github.com/jlevy/metaproc/pull/96 at b2be344. Identity names are derived only from the current spec and include CLI aliases. Reproduced removal of an identity declaration succeeding, removal/change/restoration of the flag establishing a different value, and renaming AS_OF to AS_OF_DATE refusing despite unchanged logical as_of and value. Persist canonical logical bindings and their policy; define explicit transitions; compare aliases only after logical input resolution. Do not retroactively claim historical guarantees for old runs.
