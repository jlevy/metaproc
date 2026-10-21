---
type: is
id: is-01m4nngfbvesz19razr625z29e
title: "PR #105 C3: check_auth omits the compute-ADC branch (CLOUD_SHELL / GEMINI_CLI_USE_COMPUTE_ADC)"
kind: bug
status: open
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m4nngebtw2grshwdvpd3g8np
created_at: 2026-10-11T14:22:45.626Z
updated_at: 2026-10-11T14:34:06.284Z
---
Low, outcome pre-existing. https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5483638299. getAuthTypeFromEnv returns compute ADC when CLOUD_SHELL or GEMINI_CLI_USE_COMPUTE_ADC is exactly true; gemini_cli.py:867-876 reports none. Docstring :797-801, runbook :294-299, CHANGELOG :131-134 claim check_auth reports the mode the CLI selects. Fix: add a compute-default-credentials branch after GEMINI_API_KEY, or say the order covers the four modes the adapter supports.

## Notes

Deferred from PR #105 (review C, C3). The outcome is pre-existing: before and after the PR, check_auth reports 'none' where gemini-cli would select compute ADC (CLOUD_SHELL or GEMINI_CLI_USE_COMPUTE_ADC exactly 'true'); no launch is gated on check_auth (consumers: auth-check, auth, variants). Waits on a decision whether Metaproc supports compute default credentials as a Gemini auth mode (a new check_auth branch after GEMINI_API_KEY, auth_info and runbook entries, and a check that the 0.40 floor has the same getAuthTypeFromEnv branch), versus narrowing the docstring, runbook (credential-setup.runbook.md:294-299) and CHANGELOG to the four modes the adapter supports.
