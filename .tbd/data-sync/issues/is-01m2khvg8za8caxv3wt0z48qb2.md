---
type: is
id: is-01m2khvg8za8caxv3wt0z48qb2
title: "PR #79 review R6: heuristic env-name redaction needs a minimum value length and must skip boolean/numeric literals"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m2khtp39hxxwknrtk7gzqpfs
created_at: 2026-09-15T22:09:00.189Z
updated_at: 2026-09-15T22:56:24.788Z
closed_at: 2026-09-15T22:56:24.786Z
close_reason: "Fixed in 8b362ba: name-heuristic redaction requires >=8 chars and non-boolean/non-numeric values; declared and SECRET-kind vars unconditional. Tests: test_short_boolean_and_numeric_values_under_secret_like_names_are_not_redacted, test_declared_and_framework_secrets_are_redacted_whatever_their_length."
resolution: null
duplicate_of: null
---
PR #79 (https://github.com/jlevy/metaproc/pull/79#issuecomment-5688716379), Medium. src/metaproc/runtime/diagnostics.py:60-77 redacts any value under a *TOKEN/KEY/SECRET/AUTH name. NPM_CONFIG_ALWAYS_AUTH=true mangles diagnostics; SKIP_AUTH=9 flips a 429 handler verdict RETRY->FAIL and rate_limited->unknown; BUILD_KEY=9 downgrades rate_limited to crash. Fix: for the name-heuristic branch only, require >=8 chars and skip boolean/numeric literals; keep declared METAPROC_GCP_SECRET_REFS_JSON targets and SECRET-kind framework variables unconditional. Add both env maps to tests/test_command_diagnostics.py.
