---
type: is
id: is-01m355rshwjr73hn5zd2cjp1bm
title: "PR 92 R3: clean writability probes after write failure"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m355h6et8q600r0pv9c30k49
created_at: 2026-09-22T18:24:08.243Z
updated_at: 2026-09-22T18:24:08.243Z
---
PR 92 review R3: engine/preflight.py:143 and commands/auth_check.py:704 leak descriptors and files when os.write fails. Set always_clean=True and test both paths.
