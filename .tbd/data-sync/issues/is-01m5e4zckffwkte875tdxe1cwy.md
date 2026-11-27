---
type: is
id: is-01m5e4zckffwkte875tdxe1cwy
title: "PR #107 F4: tail replays a transcript twice at every step end"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m5dmedq0w40wpkbx1qt44zr7
created_at: 2026-10-21T02:34:49.314Z
updated_at: 2026-10-21T02:34:49.314Z
---
An adversarial review of PR #107 measured this on a 419 KB Pi log.

`try_compact_log` (`src/metaproc/logutil/compaction.py:613-640`) performs two physical replacements back to back: `compact_log` rewrites the plain file by atomic rename (new inode, header line added), then `gzip_text_path` publishes the `.gz` and unlinks the plain file. The `tail` reader (`src/metaproc/logutil/parsing.py:1744-1753`) resets and replays on any identity change, so an operator watching a step sees the entire transcript re-printed twice, each with a "Log storage changed" warning, whenever the gzip pass takes longer than the poll gap.

The replay itself is deliberate and documented (it rebuilds per-file usage metadata, and the warning says previously emitted events may repeat). The double replay is not: the existing tests assert exactly one replay because they call `try_compact_log` without polling in between, so the two-replacement sequence is untested and undocumented.

Candidates, in rough order of preference:
1. Suppress display of replayed events while still rebuilding metadata from them. This removes the operator-visible duplication entirely rather than halving it, and is the real fix; the reader would need to distinguish a replay pass from a first read.
2. Make compaction publish the compressed file directly so only one replacement is observable.
3. Document it and accept it.

Found during the pre-merge review of PR #107; not a crash, but the first thing an operator notices.
