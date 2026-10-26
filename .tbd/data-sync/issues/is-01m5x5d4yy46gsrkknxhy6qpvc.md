---
type: is
id: is-01m5x5d4yy46gsrkknxhy6qpvc
title: Publish the v0.6.0 release
kind: task
status: closed
priority: 1
version: 2
labels: []
dependencies: []
created_at: 2026-10-26T22:30:56.733Z
updated_at: 2026-10-26T22:55:11.247Z
closed_at: 2026-10-26T22:55:11.246Z
close_reason: "Published. Tag v0.6.0 points at 4289836 (merge of PR 120, tip of main). Evidence in docs/publishing.md order: make verify green on an identical tree (c91f734, 5671 passed, 15 skipped); CI green on 4289836 (lint, test 3.14 pure, test 3.14 c, distribution); publish.yml run 40415435300 succeeded including its own make verify gate; PyPI shows 0.6.0 with requires-python >=3.14,<4.0, AGPL-3.0-or-later, the Python 3.14 classifier, homepage/issues/repository/changelog links, wheel and sdist, and the release notes as its description; the four step-9 uvx smoke tests (--help, --version, skill metaproc, env --template) all passed from a clean directory outside the checkout, with --version reporting 0.6.0. TODO.md Current Release moved to v0.6.0 in c39938d. Shipped without PR 117, which stays open under review. mp-mo74 (GCP image interpreter) remains unverified for a second consecutive release."
resolution: null
duplicate_of: null
---
Release v0.6.0 from main.

Delta since v0.5.0 (36c8099 at prep time): the gcp run file console and its live view, Softschema 0.9 validation execution evidence, the frontmatter-format 0.5 reader requirement, the mapped-composite scope ceiling following the run ceiling, and three fixes that were live in v0.5.0 (guarded frontmatter repair, durable terminal status timestamps, pure-emitter safe YAML writers).

A minor, not a patch: the file console adds CLI surface (gcp run --console/--watch-run, gcp logs --errors/--follow/--cloud-logging, gcp status --run-status), the softschema and frontmatter-format floors rise, and the mapped-composite scope ceiling changes concurrency for an existing process.

Done when v0.6.0 is tagged from main, publish.yml succeeds, PyPI metadata is checked, the docs/publishing.md step-9 smoke tests pass against the released version, and TODO.md Current Release names v0.6.0.
