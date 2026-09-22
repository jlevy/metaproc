---
type: is
id: is-01m353aeczswk6fxynyghjgt8v
title: Stop log compaction from silently truncating a live log
kind: bug
status: open
priority: 1
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:41:20.927Z
updated_at: 2026-09-22T17:41:20.927Z
---
`logutil/compaction.py` reads a log, transforms it, and republishes it with
`atomic_output_file` (`:275`, `:329-330`). The publication is atomic. The cycle around it
is not synchronized with anything appending to that log.

Two distinct losses:

1. Any record appended between `read_text()` and the rename is gone.
2. Worse: the producing subprocess holds an **open append fd on the original inode**
   (`runpool/backend.py:264` / `:302`). `atomic_output_file` commits with `Path.replace`,
   which unlinks that inode. Every byte the child writes afterwards goes to a file
   nothing can open — the log silently stops growing for the rest of the step.

`metaproc compact-logs <run-dir>` (`commands/compact_logs.py:55`) walks a whole tree with
no liveness filter at all, so pointing it at a running batch to reclaim disk triggers
exactly this.

The sibling rewriter `commands/gzip_text.py` already has both guards this one lacks: a
60-second mtime liveness window (`--exclude-active`) and a post-transform stability
re-stat. Port them:

- Before the read: `st_before = path.stat()`; skip when
  `time.time() - st_before.st_mtime < 60.0` and `exclude_active` is set.
- Before the rename: re-`stat` and refuse when `(st_size, st_mtime_ns)` moved.
- Thread `exclude_active: bool = True` through `compact_log` → `compact_logs_path` →
  `plan_compaction`. `try_compact_log` passes `exclude_active=False`, using the same
  argument `gzip_text.py:637` already makes: it knows the writer exited.

Cost of the fix: files touched in the last 60s stop being compacted, so an operator
compacting immediately after a run sees fewer files processed. That needs a flag or a
note.

Two smaller items in the same file:

- `:329-335` passes `backup_suffix` unconditionally, then unlinks the backup four lines
  later when `keep_original` is false. `python-modern-guidelines` is explicit that strif
  moves the destination *to* the backup before installing the new file, so the log
  briefly does not exist and a tailer or the browser gets a spurious `FileNotFoundError`
  — for nothing, on the default path. Make it `backup_suffix = ".bak" if keep_original
  else None` and guard the unlink. Verified the `.bak` name is identical either way.
- `:118` — `_walk_jsonl_files` uses `os.walk`'s default `onerror=None`, which silently
  drops directory-read errors, so `CompactionSweepSummary.matched` undercounts and the
  CLI prints a clean success for a sweep that never saw part of the tree. Ordering and
  symlink handling in this function are already correct.
