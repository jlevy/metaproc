---
type: is
id: is-01m0typvme3hfhhrkg8d0znjcg
title: "PR #38 review 2: make retired Filestore path aliases actionable on resume"
kind: bug
status: closed
priority: 1
version: 3
labels: []
dependencies: []
parent_id: is-01m0typ5swnqc9v7gee2ymkjs9
created_at: 2026-08-24T22:36:56.846Z
updated_at: 2026-08-24T22:56:48.499Z
closed_at: 2026-08-24T22:56:48.499Z
close_reason: Fixed in 809fccc; exact-head CI run 32786763844 passed all five jobs and disposition published at issuecomment-5402607487.
resolution: null
duplicate_of: null
---
Review finding 2 at issuecomment-5402359572. src/metaproc/commands/run_process.py:619-641 and tests/test_run_config.py:262 intentionally reject workstation SSHFS aliases for canonical /mnt/filestore paths, but the resume mismatch gives no migration remedy. Preserve the no-compatibility decision while making the error explain the removed alias and supported recovery.
