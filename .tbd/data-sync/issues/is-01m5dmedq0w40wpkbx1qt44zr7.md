---
type: is
id: is-01m5dmedq0w40wpkbx1qt44zr7
title: "Address PR #107 review: artifact compression codecs"
kind: task
status: open
priority: 2
version: 6
labels: []
dependencies: []
child_order_hints:
  - is-01m5dmeza78g84sp6n95jjx6x6
  - is-01m5dmeznh3wdg4h9rt1sknetk
  - is-01m5dmf0426xcenc926ajgrrxr
created_at: 2026-10-20T21:45:56.191Z
updated_at: 2026-10-20T21:46:28.261Z
---
Review of PR #107, https://github.com/jlevy/metaproc/pull/107.

jlevy's comment on `logutil/parsing.py` (the `size = artifact.logical_size if artifact.is_gzip else stat.st_size` line): encode compressed and uncompressed size separately from the codec encodings, support zstd seamlessly beside gzip, and default to zstd in future. Addressed in the PR: a closed codec registry in `metaproc.io.compression`, `gz_io.py` renamed to `artifact_io.py`, codec-free `disk_size`/`logical_size`, `is_gzip` removed, one `ArtifactDecodeError(OSError)`, and a design record at `docs/project/design/artifact-compression.md`. Python floor raised to 3.14 so `compression.zstd` is stdlib with no third-party dependency.

A concurrent correctness review found three bugs in the same reader, all fixed in the PR: decode failures escaping `except OSError`, a size trailer below the read offset replaying a transcript forever, and identity taken from a path stat rather than the opened handle.

## Notes

Children are the deferred items. `DEFAULT_WRITE_CODEC` stays gzip until they clear.
