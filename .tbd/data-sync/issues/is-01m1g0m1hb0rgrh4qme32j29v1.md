---
type: is
id: is-01m1g0m1hb0rgrh4qme32j29v1
title: Normalize agent CLI adapter module and version contracts
kind: chore
status: closed
priority: 2
version: 3
labels:
  - adapters
  - testing
dependencies: []
created_at: 2026-09-02T02:54:24.810Z
updated_at: 2026-09-02T03:19:18.112Z
closed_at: 2026-09-02T03:19:18.111Z
close_reason: Normalized Claude, Codex, Gemini, and Pi adapter module naming; removed copied current-version literals from tests; updated docs; full local verification and all pull-request checks passed.
resolution: null
duplicate_of: null
---
Rename the Claude, Codex, and Gemini adapter modules to the same <executable>_cli.py convention already used by Pi. Remove copied release literals from adapter tests so each CLI pin has one authoritative declaration while tests continue to verify version parsing, mismatch reporting, and setup-hint behavior. Update imports and documentation and land the change on the existing agent CLI pin-refresh pull request.
