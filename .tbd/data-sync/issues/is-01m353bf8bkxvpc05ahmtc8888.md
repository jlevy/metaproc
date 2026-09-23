---
type: is
id: is-01m353bf8bkxvpc05ahmtc8888
title: Clean up the wheel and workspace temp directories gcp-run leaves behind
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:41:54.571Z
updated_at: 2026-09-22T17:41:54.571Z
---
`cloud/gcp/dispatch_artifacts.py:89` and `:244` each call `tempfile.mkdtemp(...)` with no
registered cleanup.

Every `metaproc gcp-run` dispatch leaves behind:

- a `metaproc-wheel-*` directory holding a full wheel, and
- a `metaproc-workspace-*` directory holding a gzipped copy of the working tree,
  including untracked-but-not-ignored files.

On a long-lived operator machine or a reused runner these accumulate until the disk
fills, and the workspace tarballs are the more interesting half: they are a copy of
whatever was in the tree at dispatch time.

`filesystem-rules` asks that temporary files be "either cleaned up deterministically or
in a documented recoverable state". Neither holds — nothing removes them and nothing
says they are there.

Fix: `strif.temp_output_dir` (a context manager) or an explicit `finally`. Note the
default-argument shape: both functions accept an `out_dir` and only fall back to
`mkdtemp` when it is absent, so the cleanup must be conditional on having created the
directory.

Adjacent, same area — `cloud/gcp/gcp_credentials.py:50-52`:
`with suppress(OSError): path.unlink()` on the decoded service-account key. If removal
fails the key stays in `/tmp` for the life of the machine, and both the `atexit` hook and
`reset()` report success. A blanket suppress on a secret cleanup should at least
`log.warning`.
