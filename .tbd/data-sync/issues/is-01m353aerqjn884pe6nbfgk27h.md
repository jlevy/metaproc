---
type: is
id: is-01m353aerqjn884pe6nbfgk27h
title: Make gzip-text staging exclusive and its metadata policy explicit
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:41:21.303Z
updated_at: 2026-09-22T17:41:21.303Z
---
`commands/gzip_text.py` is otherwise the reference implementation of atomic publication
in this repository — temp file in the destination directory, full write, `os.fsync` (so
it promises crash durability, not just atomic visibility), a byte-fidelity round-trip
check, a source-stability re-stat, `os.replace`, and only then the source delete. Three
gaps:

1. **The staging name is fixed and the create is not exclusive** (`:237-249`). The
   partial path is `<src>.gz.partial` with no random component; it is cleared by
   `if tmp.exists(): tmp.unlink()` and then opened with
   `os.O_WRONLY | os.O_CREAT | os.O_TRUNC` — **no `O_EXCL`**. Two concurrent
   `metaproc gzip-text` runs over the same tree, or a CLI run racing the post-run
   auto-compression (they share `gzip_text_path`), both open and truncate the same
   partial and can publish a corrupt `.gz`. The round-trip verification at `:270-288`
   reads that same shared path, so it can pass against the *other* writer's bytes.
   `--exclude-active`'s 60-second window guards against a live producer of the source,
   not against a second compressor. Fix: `O_CREAT | O_EXCL` on a randomized suffix, and
   treat `FileExistsError` as "another compressor owns this file, skip". Compare strif,
   which appends `new_uid()` for exactly this reason.

2. **Metadata policy is implicit** (`:249`, `:309-313`). The destination is created at a
   hardcoded `0o644` and only mtime/atime are restored, with the failure swallowed. The
   source's mode, ownership, and xattrs are dropped when `foo.jsonl` becomes
   `foo.jsonl.gz`. For run-artifact logs that is probably right — but
   `filesystem-rules` asks for the decision to be stated. Either `shutil.copystat` after
   the `os.replace`, or a comment saying the mode is deliberately normalized.

3. **Traversal errors are dropped and the order is observable** (`:135`).
   `os.walk(root, followlinks=False)` with the default `onerror=None` silently discards
   `os.scandir` failures, so the command reports "Done: N gzipped, K skipped" over a tree
   it only partly saw. The result list is also unsorted while the order is visible: the
   `gzipped: <path>` lines and the dry-run "would gzip" list (capped at 50, so *which*
   50) both follow it. Pass an `onerror` that raises or collects, and `return sorted(out)`.
   The `followlinks=False` plus `is_symlink()` checks are a correct explicit symlink
   decision — keep them.
