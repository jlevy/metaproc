---
type: is
id: is-01kz2hvnprnmmcznb73w1yc9cz
title: Review and land company-research resource and compact-ID infrastructure
kind: feature
status: closed
priority: 1
version: 5
labels:
  - resources
  - ids
  - pull-request
dependencies: []
created_at: 2026-08-03T00:54:54.935Z
updated_at: 2026-08-25T17:00:38.558Z
closed_at: 2026-08-03T08:34:18.606Z
close_reason: "Superseded on 2026-08-03 by focused spec/epic mp-0sia after PR #9 landed. The three unresolved resource correctness beads were reparented; unrelated PR #6 scope is intentionally excluded."
---
Publish the company-research resource and compact-ID infrastructure as an explicit Metaproc pull request. It adds self-identifying and compact typed IDs, exact provider meters, budgets and finalization, agent-output capture, status repair, documentation, and regression coverage. Run make verify under the pinned toolchain, push, open against main, and wait for public CI before downstream consumers repin.

## Notes

Opened a ready-for-review public pull request; lint, distribution, supported-Python CI, standalone audit, and distribution checks passed. Keep open until upstream merge and downstream consumers can repin to a release.
