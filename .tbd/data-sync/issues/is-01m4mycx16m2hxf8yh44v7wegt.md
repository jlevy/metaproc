---
type: is
id: is-01m4mycx16m2hxf8yh44v7wegt
title: "PR #105 A3: per-launch Gemini homes kept until process exit, size unmeasured"
kind: bug
status: open
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m4mybyqptgk7zm6660wmwtzb
created_at: 2026-10-11T07:38:51.296Z
updated_at: 2026-10-11T13:37:09.481Z
---
Low. Review A https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482267623. adapters/gemini_cli.py:275-278 creates one home per launch inside the process-scoped TemporaryDirectory; nothing reclaims it when the child exits. Fix (pick one): measure and state a sizing rule, or reclaim a launch's home once its child exits.

## Notes

Deferred from PR #105 (review A, A3). Waits on a design decision: how a launch's private Gemini home is tied to its child's lifetime, with a launch-completion hook in the adapter protocol that every launcher (run-process, run-parallel, run-step both modes, auth-check, probe-tool-use) calls after the child exits, keeping the home on failure when its session state is wanted as evidence. The same lifecycle question covers mp-tibq (a detached run-step child loses its home when Metaproc exits). Measuring a representative home needs a live Gemini session, which the addressing pass did not run. Meanwhile the credentials runbook (commit 1b0b32d) states one home per launch, what each retains, a private TMPDIR, and manual removal after a kill.
