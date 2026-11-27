---
type: is
id: is-01m5dmeznh3wdg4h9rt1sknetk
title: "PR #107 F2: metabrowser cannot read .zst, so zstd transcripts lose the browser view and charts"
kind: bug
status: open
priority: 2
version: 2
labels: []
dependencies:
  - type: blocks
    target: is-01m5dmf0426xcenc926ajgrrxr
parent_id: is-01m5dmedq0w40wpkbx1qt44zr7
created_at: 2026-10-20T21:46:14.576Z
updated_at: 2026-10-20T21:46:28.941Z
---
Metaproc readers (\`tail\`, \`status\`, \`stats\`, \`trace\`, operations summary, \`metaproc.io\`) all handle \`.zst\` after PR #107. Metabrowser has its own \`ArtifactPath\` (\`metabrowser/gz_io.py\`) that reads \`.gz\` and \`.zlib\` but not \`.zst\`, and the Metaproc plugin chart handler uses metabrowser class, so a \`.zst\` transcript renders as opaque binary with no charts.

This is the first gate on flipping \`DEFAULT_WRITE_CODEC\` to zstd, and the reason PR #107 leaves the write default at gzip. The fix is in the metabrowser repository (jlevy/metabrowser), not here; this bead tracks the dependency. The operator reference now states the gap.
