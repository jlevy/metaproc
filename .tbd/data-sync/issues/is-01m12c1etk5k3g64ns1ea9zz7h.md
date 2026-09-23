---
type: is
id: is-01m12c1etk5k3g64ns1ea9zz7h
title: Sweep pre-existing prose for em dashes per common-doc-guidelines
kind: chore
status: open
priority: 3
version: 1
labels:
  - docs
dependencies: []
created_at: 2026-08-27T19:44:36.691Z
updated_at: 2026-08-27T19:44:36.691Z
---
The documentation guidelines say to avoid em dashes in prose, preferring full stops, commas, colons, and semicolons, and to write the rare keepers unspaced. All prose authored in the documentation reorganization (PR #51) now complies, but pre-existing prose does not: metaproc-operator-reference.md alone has 17 spaced em dashes, and the arch docs, developer guide, runbooks, and docs/development.md have more. Sweep them file by file, rewriting rather than mechanically substituting, and leave code blocks and quoted output alone.
