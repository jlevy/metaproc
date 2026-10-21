---
type: is
id: is-01m5fzzgbg2k0p50wek6m5mbz4
title: v0.5.0 release
kind: task
status: closed
priority: 1
version: 3
labels: []
dependencies: []
created_at: 2026-10-21T19:45:59.152Z
updated_at: 2026-10-21T20:10:23.441Z
closed_at: 2026-10-21T20:10:23.440Z
close_reason: "Published. Tag v0.5.0 points at 0714762 (merge of PR 110, tip of main). Evidence in docs/publishing.md order: make verify green on an identical tree (5e091f0, 5536 passed); CI green on 0714762 (lint, test 3.14, distribution); publish.yml run 39667276339 succeeded including its own make verify gate; PyPI shows 0.5.0 with requires-python >=3.14, AGPL-3.0-or-later, Python 3.14 classifier, source/issue links, wheel and sdist; the four step-9 uvx smoke tests passed from a clean directory outside the checkout. TODO.md Current Release moved to v0.5.0."
resolution: null
duplicate_of: null
---
Release v0.5.0: a minor, because it raises the Python floor to 3.14 (stdlib compression.zstd).

Records: CHANGELOG [0.5.0], docs/project/releases/v0.5.0.md, docs/publishing.md step 5. Compatibility leads with the 3.14 floor, the unverified GCP image interpreter (mp-mo74), Metabrowser not reading .zst, and mapped agent steps on profiles with different pool resources now failing in one local run.

Done when v0.5.0 is tagged from main, publish.yml succeeds, PyPI metadata is checked, the docs/publishing.md step-9 smoke tests pass against the released version, and TODO.md § Current Release names v0.5.0.
