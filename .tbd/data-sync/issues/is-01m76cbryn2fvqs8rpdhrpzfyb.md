---
type: is
id: is-01m76cbryn2fvqs8rpdhrpzfyb
title: Fail the supply-chain check on expired third-party exclude-newer-package entries
kind: task
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m76a31xm6ap52ytabzbmhdt1
created_at: 2026-11-11T22:41:23.411Z
updated_at: 2026-11-11T22:41:23.411Z
---
Deferred from PR #122 (https://github.com/jlevy/metaproc/pull/122), suggestion A4 of review A (https://github.com/jlevy/metaproc/pull/122#pullrequestreview-5865991209).

devtools/check_supply_chain.py:100-103 (_check_uv) checks only that exclude-newer is "14 days". Removing a temporary third-party cool-off exception depends on someone rereading a comment, which is how the certifi pin in A2 outlived its 2026-11-03 removal date. Make _check_uv fail when an exclude-newer-package timestamp is more than 14 days old for a package outside the first-party list in SUPPLY-CHAIN-SECURITY.md:52-127. Review B notes B1 is a case this check would have caught.
