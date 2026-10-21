---
type: is
id: is-01m4nnggd9yzbtvbqkc5yzkdc3
title: "PR #105 C6: operator reference does not warn that a detached run-step loses its Gemini home (mp-tibq)"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m4nngebtw2grshwdvpd3g8np
created_at: 2026-10-11T14:22:46.697Z
updated_at: 2026-10-11T14:37:38.095Z
closed_at: 2026-10-11T14:37:38.094Z
close_reason: "Fixed in d325c83: operator reference says to use run-step --wait for a Gemini step run alone. Underlying fix stays with mp-tibq/mp-1qjt. Reply https://github.com/jlevy/metaproc/pull/105#issuecomment-6110135184"
resolution: null
duplicate_of: null
---
Low. https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5483638299. Detached run-step (default without --wait, commands/run_step.py:90) exits after launch_step; the process-scoped TemporaryDirectory holding the private home (gemini_cli.py:297-300) is removed. metaproc-operator-reference.md mentions run-step at :686 only. Fix: one sentence saying Gemini steps run through run-step need --wait until mp-1qjt and mp-tibq tie the home to the child; or warn in detached mode.
