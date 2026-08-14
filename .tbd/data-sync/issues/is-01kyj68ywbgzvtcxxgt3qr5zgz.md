---
type: is
id: is-01kyj68ywbgzvtcxxgt3qr5zgz
title: Remove private repository references from public migration
kind: task
status: closed
priority: 0
version: 4
spec_path: docs/project/specs/done/plan-2026-07-26-standalone-extraction.md
labels: []
dependencies: []
parent_id: is-01kygat035xcheze599f3yxqrb
created_at: 2026-07-27T16:24:36.490Z
updated_at: 2026-08-09T18:56:59.150Z
closed_at: 2026-07-27T16:27:36.889Z
close_reason: Removed the public PR cross-references, confirmed the extraction tree was already clean, sanitized the issue-sync branch and its reachable history, and passed local marker searches plus the existing public-hygiene gate.
---
Remove every reference to the private source application and its repository from the standalone Metaproc tree and public pull request metadata; validate using local-only marker searches and the existing public-hygiene gate.
