---
type: is
id: is-01kyxpharxskce7vawekfey3n9
title: Migrate checked JavaScript to noImplicitAny
kind: task
status: open
priority: 2
version: 1
labels:
  - linting
  - typescript
dependencies: []
parent_id: is-01kyxndt8jqeyf92cp0c2h3krq
created_at: 2026-08-01T03:40:26.780Z
updated_at: 2026-08-01T03:40:26.780Z
---
Eliminate the legacy checked-JavaScript noImplicitAny=false escape hatch across the Metabrowser plugin and DOM harness. The current strict-mode probe reports roughly 454 diagnostics, so this is an explicit whole-project migration rather than incidental churn in PR #3. Keep the tracker referenced beside the tsconfig exception, add types incrementally, and turn the compiler option on once the tree is clean.
