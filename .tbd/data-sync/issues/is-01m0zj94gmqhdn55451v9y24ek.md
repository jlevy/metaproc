---
type: is
id: is-01m0zj94gmqhdn55451v9y24ek
title: Preserve multiline CLI API errors during retry classification
kind: bug
status: closed
priority: 1
version: 5
labels:
  - retry
  - observability
dependencies: []
created_at: 2026-08-26T17:35:56.436Z
updated_at: 2026-08-26T17:54:26.299Z
closed_at: 2026-08-26T17:54:26.299Z
close_reason: Fixed at 293e49b; complete local and GitHub validation is green.
resolution: null
duplicate_of: null
---
When a CLI writes a pretty-printed multiline API error, terminal error extraction can select only a closing delimiter from the log tail. The resulting opaque exit message can misclassify a permanent client or authorization error as retryable. Add a narrow generic regression and retain the meaningful terminal error text without provider- or consumer-specific behavior.

## Notes

Implemented the final narrow provider-neutral fix in src/metaproc/engine/retry.py. Structured terminal result events with status=error now expose error.message to the existing classifier, and raw fallback ignores bare container delimiters so they cannot mask a preceding diagnostic. The regression uses a generic multiline 403 plus terminal structured result and proves Permission denied reaches the permanent classifier. Validation at commit 293e49bf6ffc194938be0e4648472ffce08432d7: 90 focused retry tests passed; Ruff and BasedPyright clean; full make verify passed with 4,440 tests passed and 8 skipped; all five required GitHub CI jobs pass. No provider-specific policy or new retry subsystem was added.
