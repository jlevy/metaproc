---
type: is
id: is-01m4nngfqk3x6ahefh84vm7azh
title: "PR #105 C4: GCA refusal reads the seed env before the step env is merged"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m4nngebtw2grshwdvpd3g8np
created_at: 2026-10-11T14:22:46.001Z
updated_at: 2026-10-11T14:37:37.770Z
closed_at: 2026-10-11T14:37:37.769Z
close_reason: "Fixed in 9c298f0 (option 1): runbook GOOGLE_GENAI_USE_GCA row states both step-env edges. Reply https://github.com/jlevy/metaproc/pull/105#issuecomment-6110135184"
resolution: null
duplicate_of: null
---
Low. https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5483638299. engine/runtime.py:262-264, commands/run_process.py:3260-3262, commands/run_parallel.py:1453-1454, commands/run_step.py:581-585 call prepare_env before env.update(step env). A step env that sets GOOGLE_GENAI_USE_GCA=true is not checked; one that blanks it is still refused. Fix: state both edges in credential-setup.runbook.md:304, or move the check to the merged env through an adapter hook the four launch sites call after env.update.
