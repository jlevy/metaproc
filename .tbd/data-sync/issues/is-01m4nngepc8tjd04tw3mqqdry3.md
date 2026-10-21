---
type: is
id: is-01m4nngepc8tjd04tw3mqqdry3
title: "PR #105 C1: GEMINI_FORCE_ENCRYPTED_FILE_STORAGE counted as an unconditional Login with Google source"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m4nngebtw2grshwdvpd3g8np
created_at: 2026-10-11T14:22:44.939Z
updated_at: 2026-10-11T14:37:37.147Z
closed_at: 2026-10-11T14:37:37.146Z
close_reason: "Fixed in be56b5a: keychain flag no longer a Login with Google source; remedy names it as host-dependent with selectedType route. Tests fail before, pass after. Reply https://github.com/jlevy/metaproc/pull/105#issuecomment-6110135184"
resolution: null
duplicate_of: null
---
Low. https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5483638299. adapters/gemini_cli.py:432-447 (_gca_login_source) and :827-839 (check_auth) treat the flag as ready; env_vars.py:214-217, credential-setup.runbook.md:304, arch-authentication.md:491 say it keeps the login in the OS keychain. Without a usable keychain gemini-cli falls back to FileKeychain under homedir()/.gemini, which honours GEMINI_CLI_HOME (the empty private home). Fix: drop it from _gca_login_source and name it in _GCA_REMEDY, or qualify the texts.
