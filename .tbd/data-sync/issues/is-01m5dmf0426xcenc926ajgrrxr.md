---
type: is
id: is-01m5dmf0426xcenc926ajgrrxr
title: "PR #107 F3: flip DEFAULT_WRITE_CODEC to zstd once its gates clear"
kind: task
status: open
priority: 3
version: 1
labels: []
dependencies: []
parent_id: is-01m5dmedq0w40wpkbx1qt44zr7
created_at: 2026-10-20T21:46:15.036Z
updated_at: 2026-10-20T21:46:15.036Z
---
jlevy on PR #107: "where possible dfeault to zstd in the future as it is much more efficient". PR #107 makes this a one-line change (\`DEFAULT_WRITE_CODEC\` in \`src/metaproc/io/compression.py\`), with reading symmetric across codecs so mixed run directories are ordinary rather than an error state.

Gates, recorded in \`docs/project/design/artifact-compression.md\`:
1. Metabrowser reads \`.zst\` (F2).
2. The 3.14 floor is released and the cloud image confirmed (F1).
3. Any consumer tooling that globs \`*.jsonl.gz\` directly is updated.

Measured on a synthetic highly-repetitive JSONL sample during review: zstd 307x versus gzip 138x, about 2.2x better. Real transcripts will compress less dramatically; re-measure on real artifacts before deciding the level.

Also tracked in \`docs/project/design/backlog/arch-file-io-utilities-backlog.md\`.
