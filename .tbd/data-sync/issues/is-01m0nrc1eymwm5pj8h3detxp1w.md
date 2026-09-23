---
type: is
id: is-01m0nrc1eymwm5pj8h3detxp1w
title: "PR #26 review L1: lossless and byte-identical claims have real exceptions"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m0nrb7r4e9ejhv5yp3q4amm0
created_at: 2026-08-22T22:09:58.750Z
updated_at: 2026-08-22T22:32:56.849Z
closed_at: 2026-08-22T22:32:56.849Z
close_reason: null
---
new_yaml(typ='rt') expands YAML anchors (vanilla ruamel rt does not); True/TRUE -> 'true'; +1_000 -> '1_000'; CRLF body rewritten to LF. Either handle or record as refusal tests and narrow the docstring at :15-17.
