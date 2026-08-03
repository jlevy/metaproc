---
type: is
id: is-01kz2hvnprnmmcznb73w1yc9cz
title: Review and land company-research resource and compact-ID infrastructure
kind: feature
status: in_progress
priority: 1
version: 3
labels:
  - resources
  - ids
  - pull-request
dependencies: []
created_at: 2026-08-03T00:54:54.935Z
updated_at: 2026-08-03T01:15:39.646Z
---
Publish the eight-commit codex/company-research-infrastructure stack as an explicit Metaproc PR. It adds self-identifying and compact typed IDs, exact provider meters, budgets/finalization, agent-output capture, status repair, documentation, and regression coverage. Run make verify under the pinned Node 24.18/npm 11.10 environment, push, open the PR against main, and wait for CI before the downstream trading PR repins to Metaproc main.

## Notes

Opened ready-for-review PR https://github.com/jlevy/metaproc/pull/6 at 62334b7. GitHub reports MERGEABLE; lint, distribution, and Python 3.12/3.13/3.14 CI all pass. Standalone audit and distribution checks pass. Keep open until upstream merge and downstream repin.
